from flask import Flask, request, jsonify, render_template
from auth import initAuth, loginRequired, getCurrentUser
import requests
import base64
from datetime import datetime
from zoneinfo import ZoneInfo
import markdown
import json

# helper function to debug responses
def saveJSON(filename, data):
    with open(f"{filename}.json", 'w') as F:
        json.dump(data, F)

app = Flask(__name__)
app.secret_key = "somesecret"

# Azure AD Configuration, skip if not needed
app.config['AZURE_CLIENT_ID'] = "clientID"
app.config['AZURE_CLIENT_SECRET'] = "secret"
app.config['AZURE_TENANT_ID'] = "tenantID"
app.config['REDIRECT_URI'] = 'https://ezpsa.domain.com/auth'

# Initialize authentication, comment out if not needed
initAuth(app)

# Connectwise Config
company = "company"
publicKey = "publickey"
privateKey = "privatekey"
clientId = "clientID"
baseUrl = "https://api-na.myconnectwise.net/v4_6_release/apis/3.0"

authString = f"{company}+{publicKey}:{privateKey}"
authHeader = base64.b64encode(authString.encode()).decode()

headers = {
    "Authorization": f"Basic {authHeader}",
    "ClientId": clientId,
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# global variables to store tickets
allTicketsCache = {}
lastRefreshCache = {}

@app.route("/")
@loginRequired # comment out if not needed
def index():
    return render_template("index.html")

@app.route("/newTicket")
@loginRequired
def newTicketPage():
    return render_template("newTicket.html")

@app.route("/leaderboard")
@loginRequired
def leaderboardPage():
    return render_template("leaderboard.html")

# API endpoint to retrieve all service boards and return them to the requesting client
@app.route("/api/getBoards")
@loginRequired
def getBoards():
    url = f"{baseUrl}/service/boards"
    allBoards = []
    page = 1
    while True:
        params = {
            "pageSize": 10,
            "page": page
        }
        r = requests.get(url, headers=headers, params=params)
        batch = r.json()
        if not batch:
            break
        allBoards.extend(batch)
        page += 1
    boardList = []
    for board in allBoards:
        boardList.append({
            'id': board.get('id'),
            'name': board.get('name'),
            'inactive': board.get('inactiveFlag', False)
        })
    return jsonify(boardList)

# API endpoint to get tickets for a given board, board specified by request param
@app.route("/api/getTickets")
@loginRequired
def getTickets():
    global allTicketsCache, lastRefreshCache
    boardName = request.args.get('board')
    if not boardName:
        return jsonify({"error": "board parameter is required"}), 400
    url = f"{baseUrl}/service/tickets"
    partial = request.args.get('partial', '').lower() == 'true' # allows the client to request only a partial refresh, not the whole board
    # Get or initialize this board's ticket list
    allTickets = allTicketsCache.get(boardName, [])
    lastRefresh = lastRefreshCache.get(boardName)
    if partial and allTickets:
        params = {
            "conditions": f'board/name="{boardName}" and closedFlag=false',
            "orderBy": "lastUpdated desc",
            "pageSize": 100,
            "page": 1
        }
        r = requests.get(url, headers=headers, params=params)
        newBatch = r.json()
        if newBatch:
            existingTickets = {t['id']: t for t in allTickets}
            newTicketIds = {t['id'] for t in newBatch}
            allTickets = [t for t in allTickets[:100] if t['id'] in newTicketIds] + allTickets[100:]
            existingTickets = {t['id']: t for t in allTickets}
            for newTicket in newBatch:
                ticketId = newTicket['id']
                if ticketId in existingTickets:
                    oldTicket = existingTickets[ticketId]
                    newUpdated = datetime.fromisoformat(newTicket['_info']['lastUpdated'].replace("Z", "+00:00"))
                    oldUpdated = datetime.fromisoformat(oldTicket['_info']['lastUpdated'].replace("Z", "+00:00"))
                    if newUpdated > oldUpdated:
                        allTickets = [t for t in allTickets if t['id'] != ticketId]
                        allTickets.append(newTicket)
                else:
                    allTickets.append(newTicket)
            allTickets.sort(key=lambda t: datetime.fromisoformat(t['_info']['lastUpdated'].replace("Z", "+00:00")), reverse=True)
        # Save back to cache
        allTicketsCache[boardName] = allTickets
        lastRefreshCache[boardName] = datetime.now()
    else:
        # Full refresh
        allTickets = []
        page = 1
        maxPages = 10
        while page <= maxPages:
            params = {
                "conditions": f'board/name="{boardName}" and closedFlag=false',
                "orderBy": "lastUpdated desc",
                "pageSize": 100,
                "page": page
            }
            r = requests.get(url, headers=headers, params=params)
            batch = r.json()
            if not batch:
                break
            allTickets.extend(batch)
            page += 1
        # Save to cache
        allTicketsCache[boardName] = allTickets
        lastRefreshCache[boardName] = datetime.now()
    # Generate HTML table to return to the client
    htmlOut = "<table><tr><th>ID</th><th>Title</th><th>Company</th><th>Status</th><th>Assigned</th><th>Last Updated</th><th>Action</th></tr>"
    for t in allTickets:
        status = t['status']['name']
        if status in [">Closed", ">Closed (NO EMAIL)", ">Cancelled"]:
            continue
        serviceID = t['id']
        title = f"<a href='https://na.myconnectwise.net/v4_6_release/services/system_io/Service/fv_sr100_request.rails?service_recid={serviceID}&companyName={company}' target='_blank'>{t['summary']}</a>"
        company = t['company']['name']
        assigned = t.get('owner', {}).get('name', 'Unassigned')
        lastUpdated = t['_info']['lastUpdated']
        lastUpdated = datetime.fromisoformat(lastUpdated.replace("Z","+00:00")).astimezone(ZoneInfo("America/New_York")).strftime("%B %d, %Y %I:%M %p %Z")
        viewLink = f"<a href='javascript:quickView({serviceID})'>Quick View</a>"
        closeLink = f"<a href='javascript:closeTicket({serviceID})'>Close</a>"
        htmlOut += f"<tr data-ticket-id='{serviceID}'><td>{serviceID}</td><td>{title}</td><td>{company}</td><td>{status}</td><td>{assigned}</td><td>{lastUpdated}</td><td>{viewLink}<br>{closeLink}</td></tr>"
    htmlOut += "</table>"
    return htmlOut

# endpoint for new tickets, if get request returns list of companies, if post accepts ticket form
@app.route("/api/newTicket", methods=['GET','POST'])
@loginRequired
def newTicket():
    if request.method == 'GET':
        url = f"{baseUrl}/company/companies"
        page = 1
        out = []
        while True:
            params = {"orderBy": "name asc", "page": page, "pageSize": 100}
            r = requests.get(url, headers=headers, params=params)
            batch = r.json()
            if not batch:
                break
            out.extend(batch)
            page += 1
        return jsonify(out)
    # handling post requests
    data = request.get_json()
    print(data)
    payload = {
        "summary": data.get("title"),
        "company": {"id": int(data.get("companySelect"))},
        "board": {"name": data.get("board")}, 
        "status": {"name": data.get("status")},
        "initialDescription": data.get("description")
    }
    url = f"{baseUrl}/service/tickets"
    r = requests.post(url, headers=headers, json=payload)
    if r.status_code >= 400:
        return jsonify(r.json()), 400
    return jsonify(r.json()), 200


# endpoint for closing a ticket, accepts ticketID from client and closes it with the API
@app.route("/api/closeTicket", methods=['POST'])
def closeTicket():
    data = request.get_json()
    ticketId = data.get('ticketId')
    if not ticketId:
        return jsonify({"error": "ticketId is required"}), 400
    url = f"{baseUrl}/service/tickets/{ticketId}"
    # Update the ticket status to closed (your board may use a different status name)
    payload = [
        {
            "op": "replace",
            "path": "status/name",
            "value": ">Closed (No Email)"
        }
    ]
    r = requests.patch(url, headers=headers, json=payload)
    if r.status_code >= 400:
        # try again with another status name
        payload = [
            {
                "op": "replace",
                "path": "status/name",
                "value": ">Closed"
            }
        ]
        r = requests.patch(url, headers=headers, json=payload)
        if r.status_code >= 400:
            return jsonify(r.json()), 400
    return jsonify(r.json()), 200


# quickview endpoint that returns all the notes and time entries on a ticket in descending order
@app.route("/api/quickview")
@loginRequired
def quickview():
    ticketId = request.args.get('ticketId')
    if not ticketId:
        return jsonify({"error": "ticketId is required"}), 400
    # Get ticket details
    ticketUrl = f"{baseUrl}/service/tickets/{ticketId}"
    ticketResponse = requests.get(ticketUrl, headers=headers)
    if ticketResponse.status_code >= 400:
        print(f"Ticket error: {ticketResponse.text}")
        return jsonify({"error": "Ticket not found"}), 404
    ticket = ticketResponse.json()
    ticketSummary = ticket.get('summary', 'No Summary')
    # Get URIs from _info section of the ticket, manage does not return notes or time unless specifically requested
    info = ticket.get('_info', {})
    notesHref = info.get('notes_href', '')
    timeEntriesHref = info.get('timeentries_href', '')
    allEntries = []
    # Fetch notes
    if notesHref:
        notesResponse = requests.get(notesHref, headers=headers)
        if notesResponse.status_code == 200:
            notes = notesResponse.json()
            #saveJSON("notes", notes)
            for note in notes:
                dateCreated = note.get('dateCreated', '')
                if dateCreated:
                    dateCreated = datetime.fromisoformat(dateCreated.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y %I:%M %p")
                # Convert markdown to HTML
                noteText = note.get('text', 'No text')
                noteHtml = markdown.markdown(noteText)
                allEntries.append({
                    'type': 'note',
                    'dateCreated': dateCreated,
                    'createdBy': note.get('createdBy', 'Unknown'),
                    'text': noteHtml,  
                    'rawDate': note.get('dateCreated', '')
                })
    # Fetch time entries
    if timeEntriesHref:
        timeResponse = requests.get(timeEntriesHref, headers=headers)
        if timeResponse.status_code == 200:
            timeEntries = timeResponse.json()
            #saveJSON("entries", timeEntries)
            for entry in timeEntries:
            # Get start and end times
                timeStart = entry.get('timeStart', '')
                timeEnd = entry.get('timeEnd', '')
                if timeStart:
                    timeStart = datetime.fromisoformat(timeStart.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y %I:%M %p")
                if timeEnd:
                    timeEnd = datetime.fromisoformat(timeEnd.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).strftime("%I:%M %p")
                # Format display
                if timeStart and timeEnd:
                    dateCreated = f"{timeStart} - {timeEnd}"
                else:
                    dateCreated = timeStart or "Unknown"
                notes = entry.get('notes', '').strip()
                actualHours = entry.get('actualHours', '0')
                if isinstance(actualHours, str) and ':' in actualHours:
                    actualHours = actualHours.split(':')[0]
                if notes:
                    text = markdown.markdown(notes)
                else:
                    text = f"Time entered: {actualHours} hours"
                allEntries.append({
                    'type': 'time',
                    'dateCreated': dateCreated,
                    'createdBy': entry.get('member', {}).get('name', 'Unknown'),
                    'text': text,
                    'rawDate': entry.get('timeStart', '')
                })
    # Sort by date
    allEntries.sort(key=lambda x: x.get('rawDate', ''))
    # Remove rawDate, was just for sorting
    for entry in allEntries:
        del entry['rawDate']
    return jsonify({
        'summary': ticketSummary,
        'entries': allEntries
    })

# rough leaderboard, just for fun, not used anywhere but is available to view
@app.route("/api/leaderboard")
@loginRequired
def leaderboard():
    year = request.args.get('year', '2025')
    url = f"{baseUrl}/time/entries"
    allEntries = []
    page = 1
    # Paginate through all time entries for the year
    while True:
        params = {
            "conditions": f'dateEntered>=[{year}-01-01T00:00:00Z] and dateEntered<[{int(year)+1}-01-01T00:00:00Z]',
            "pageSize": 1000,
            "page": page
        }
        r = requests.get(url, headers=headers, params=params)
        batch = r.json()
        if not batch:
            break
        allEntries.extend(batch)
        page += 1
    # Aggregate by member
    memberStats = {}
    for entry in allEntries:
        # Skip non-billable entries
        if entry.get('billableOption') != 'Billable':
            continue
        memberName = entry.get('member', {}).get('name', 'Unknown')
        # Parse hours (format: "1.0:js:1")
        invoiceHours = entry.get('invoiceHours', '0')
        hours = float(invoiceHours.split(':')[0]) if ':' in str(invoiceHours) else float(invoiceHours)
        # Parse amount (format: "150.0:js:1")
        extendedAmount = entry.get('extendedInvoiceAmount', '0')
        amount = float(extendedAmount.split(':')[0]) if ':' in str(extendedAmount) else float(extendedAmount)
        
        # Add to member's totals
        if memberName not in memberStats:
            memberStats[memberName] = {'hours': 0, 'amount': 0}
        
        memberStats[memberName]['hours'] += hours
        memberStats[memberName]['amount'] += amount
    
    # Format output
    results = []
    for member, stats in memberStats.items():
        results.append({
            'member': member,
            'hours': round(stats['hours'], 2),
            'amount': round(stats['amount'], 2)
        })
    
    # Sort by hours (descending)
    results.sort(key=lambda x: x['hours'], reverse=True)
    
    return jsonify(results)



if __name__ == '__main__':
    app.run(port=5000)
