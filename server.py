import json
from functools import wraps

import flask
import httplib2
from apiclient import discovery
from oauth2client import client

from ecessprivate.ecessdb import CLIENT_ID, CLIENT_SECRET


app = flask.Flask(__name__)


SCOPE_USEREMAIL = "userinfo.email"
SCOPE_DRIVE = "drive"

TYPE_USER = "user"
TYPE_EDITOR = "editor"

SCOPES = {
    TYPE_USER: [SCOPE_USEREMAIL],
    TYPE_EDITOR: [SCOPE_DRIVE]
}


def authenticated(*usertypes):
    """Decorator for authentication with Google OAuth2

    :param list usertype: Usertypes
    """
    def oauthorized2(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if "usertype" not in flask.session:
                flask.session["usertypes"] = []
            for usertype in usertypes:
                if usertype not in flask.session["usertypes"]:
                    flask.session["usertypes"].append(usertype)

            if 'credentials' not in flask.session:
                return flask.redirect(flask.url_for('oauth2callback'))
            credentials = client.OAuth2Credentials.from_json(
                flask.session['credentials'])
            if credentials.access_token_expired:
                return flask.redirect(flask.url_for('oauth2callback'))

            return fn(credentials, *args, **kwargs)
        return wrapped
    return oauthorized2


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


@app.route('/')
@authenticated(TYPE_USER)
def index(credentials):
    #drive_service = get_drive_service(credentials)
    #files = drive_service.files().list().execute()
    #return json.dumps(get_plus_service(credentials).people().get(userId="me").execute())
    oauth2_service = get_oauth2_service(credentials)
    return json.dumps(oauth2_service.userinfo().get().execute())


@app.route('/oauth2callback')
def oauth2callback():
    usertypes = flask.session["usertypes"]
    scopes = [scope for usertype, scopes in SCOPES.items()
              for scope in scopes if usertype in usertypes]
    scope_urls = ['https://www.googleapis.com/auth/{}'.format(scope)
                  for scope in scopes]
    print("Authenticating with scopes: {}".format(scope_urls))
    flow = client.OAuth2WebServerFlow(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scope=scope_urls,
        redirect_uri=flask.url_for('oauth2callback', _external=True)
    )
    if 'code' not in flask.request.args:
        auth_uri = flow.step1_get_authorize_url()
        return flask.redirect(auth_uri)
    else:
        auth_code = flask.request.args.get('code')
        credentials = flow.step2_exchange(auth_code)
        flask.session['credentials'] = credentials.to_json()
        return flask.redirect(flask.url_for('index'))


if __name__ == '__main__':
    import uuid

    app.secret_key = str(uuid.uuid4())
    app.debug = True
    app.run()
