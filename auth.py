from flask import session, redirect, url_for, request
from functools import wraps
from authlib.integrations.flask_client import OAuth

oauth = OAuth()

def initAuth(app):
    """Initialize OAuth with the Flask app"""
    oauth.init_app(app)
    
    oauth.register(
        name="azure",
        client_id=app.config['AZURE_CLIENT_ID'],
        client_secret=app.config['AZURE_CLIENT_SECRET'],
        server_metadata_url=f"https://login.microsoftonline.com/{app.config['AZURE_TENANT_ID']}/v2.0/.well-known/openid-configuration",
        client_kwargs={
            'scope': 'openid email profile'
        },
        redirect_uri=app.config['REDIRECT_URI']
    )
    
    # Register auth routes
    registerAuthRoutes(app)

def loginRequired(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decoratedFunction(*args, **kwargs):
        if 'user' not in session:
            # Save the original URL they were trying to access
            session['nextUrl'] = request.url
            return redirect(url_for('authLogin'))
        return f(*args, **kwargs)
    return decoratedFunction

def registerAuthRoutes(app):
    """Register authentication routes"""
    
    @app.route('/login')
    def authLogin():
        return oauth.azure.authorize_redirect()
    
    @app.route('/auth')
    def authCallback():
        try:
            token = oauth.azure.authorize_access_token()
            user = token.get('userinfo')
            
            # Store user info in session
            session['user'] = {
                'name': user.get('name'),
                'email': user.get('email'),
                'id': user.get('sub')
            }
            session['accessToken'] = token.get('access_token')
            
            # Redirect to original URL or home
            nextUrl = session.pop('nextUrl', '/')
            return redirect(nextUrl)
        except Exception as e:
            return f"Authentication failed: {str(e)}", 401
    
    @app.route('/logout')
    def authLogout():
        session.clear()
        # Optional: redirect to Microsoft logout
        logoutUrl = f"https://login.microsoftonline.com/{app.config['AZURE_TENANT_ID']}/oauth2/v2.0/logout"
        return redirect(logoutUrl + f"?post_logout_redirect_uri={request.host_url}")

# Helper function to get current user
def getCurrentUser():
    return session.get('user')