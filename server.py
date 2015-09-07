import json
import os
from functools import wraps

import flask
import httplib2
from apiclient import discovery
from oauth2client import client

from ecessprivate.ecessdb import CLIENT_ID, CLIENT_SECRET
from ecessdb import get_drive_conn

app = flask.Flask(__name__)


SCOPE_USEREMAIL = "userinfo.email"
SCOPE_DRIVE = "drive"

TYPE_USER = "user"
TYPE_EDITOR = "editor"

SCOPES = {
    TYPE_USER: [SCOPE_USEREMAIL],
    TYPE_EDITOR: [SCOPE_DRIVE]
}


class SessKeys(object):
    post_auth_redirect = "post_auth_redirect"
    usertypes = "usertypes"
    credentials = "credentials"


def authenticated(*usertypes):
    """Decorator for authentication with Google OAuth2

    :param list usertype: Usertypes
    """
    def oauthorized2(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            flask.session[SessKeys.post_auth_redirect] = flask.request.path

            if SessKeys.usertypes not in flask.session:
                flask.session[SessKeys.usertypes] = []
            for usertype in usertypes:
                if usertype not in flask.session[SessKeys.usertypes]:
                    flask.session[SessKeys.usertypes].append(usertype)

            if SessKeys.credentials not in flask.session:
                return flask.redirect(flask.url_for('oauth2callback'))
            credentials = client.OAuth2Credentials.from_json(
                flask.session[SessKeys.credentials])
            if credentials.access_token_expired:
                return flask.redirect(flask.url_for('oauth2callback'))

            return fn(credentials, *args, **kwargs)
        return wrapped
    return oauthorized2


def get_db():
    top = flask._app_ctx_stack
    if not hasattr(top, 'drive_conn'):
        top.drive_conn = get_drive_conn()
    return top.drive_conn


def _get_service(api, version, credentials):
    http_auth = credentials.authorize(httplib2.Http())
    service = discovery.build(api, version, http_auth)
    return service


def get_drive_service(credentials):
    return _get_service('drive', 'v2', credentials)


def get_plus_service(credentials):
    return _get_service('plus', 'v1', credentials)

def get_oauth2_service(credentials):
    return _get_service('oauth2', 'v2', credentials)


# @app.route('/')
# @authenticated(TYPE_USER)
# def index(credentials):
#     # drive_service = get_drive_service(credentials)
#     # files = drive_service.files().list().execute()
#     # return json.dumps(get_plus_service(credentials).people().get(userId="me").execute())
#     oauth2_service = get_oauth2_service(credentials)
#     return json.dumps(oauth2_service.userinfo().get().execute())


@app.route('/')
def index():
    return "This is an index page. If you were trying to do something" \
           " else but ended up here, please email contact@ubcecess.com."


@app.route('/student/register')
@authenticated(TYPE_USER)
def student_register(credentials):
    FORM_URL = "https://docs.google.com/forms/d/" \
    "1TUjrEqJbVIMILbItA8WG1vSIhL5VNTn3-H7sQfqzJdY/" \
    "viewform?entry.511477521={google_email}"

    oauth2_service = get_oauth2_service(credentials)
    google_email = oauth2_service.userinfo().get().execute()["email"]
    return flask.redirect(FORM_URL.format(google_email=google_email))


@app.route('/oauth2callback')
def oauth2callback():
    usertypes = flask.session[SessKeys.usertypes]
    scopes = [scope for usertype, scopes in SCOPES.items()
              for scope in scopes if usertype in usertypes]
    scope_urls = ['https://www.googleapis.com/auth/{}'.format(scope)
                  for scope in scopes]
    print("Authenticating with scopes: {}".format(scope_urls))
    flow = client.OAuth2WebServerFlow(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scope=scope_urls,
        #redirect_uri=flask.url_for('oauth2callback', _external=True)
        redirect_uri="http://app.ubcecess.com/oauth2callback"
    )
    if 'code' not in flask.request.args:
        auth_uri = flow.step1_get_authorize_url()
        return flask.redirect(auth_uri)
    else:
        auth_code = flask.request.args.get('code')
        credentials = flow.step2_exchange(auth_code)
        flask.session[SessKeys.credentials] = credentials.to_json()
        return flask.redirect(flask.session[SessKeys.post_auth_redirect])


class ReverseProxied(object):
    '''Wrap the application in this middleware and configure the
    front-end server to add these headers, to let you quietly bind
    this to a URL other than / and to an HTTP scheme that is
    different than what is used locally.

    In nginx:
    location /myprefix {
        proxy_pass http://192.168.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header X-Script-Name /myprefix;
        }

    :param app: the WSGI application

    http://flask.pocoo.org/snippets/35/
    '''
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        script_name = environ.get('HTTP_X_SCRIPT_NAME', '')
        if script_name:
            environ['SCRIPT_NAME'] = script_name
            path_info = environ['PATH_INFO']
            if path_info.startswith(script_name):
                environ['PATH_INFO'] = path_info[len(script_name):]

        scheme = environ.get('HTTP_X_SCHEME', '')
        if scheme:
            environ['wsgi.url_scheme'] = scheme
        return self.app(environ, start_response)


if __name__ == '__main__':
    import uuid

    app.secret_key = str(uuid.uuid4())
    app.debug = os.getenv("FLASK_DEBUG") == "1"
    if app.debug:
        print("WARNING: DEBUG MODE IS ENABLED!")
    app.wsgi_app = ReverseProxied(app.wsgi_app)
    app.run()
