from collections import defaultdict
import json
import os
from functools import wraps
from time import time

import arrow
import flask
import gspread
import httplib2
from apiclient import discovery
from oauth2client import client

from ecessprivate.ecessdb import APP_CLIENT_ID, APP_CLIENT_SECRET
from ecessdb import get_drive_conn

app = flask.Flask(__name__)


SCOPE_USEREMAIL = "userinfo.email"
SCOPE_DRIVE = "drive"

TYPE_USER = "user"
TYPE_EDITOR = "editor"

SCOPES = {
    TYPE_USER: [SCOPE_USEREMAIL],
    TYPE_EDITOR: [SCOPE_DRIVE, 'https://spreadsheets.google.com/feeds',
                  SCOPE_USEREMAIL]
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


def get_db(cache_period=180):
    # top = flask._app_ctx_stack
    # if not hasattr(top, 'drive_conn'):
    #     top.drive_conn = time(), get_drive_conn()
    #
    # t, gc = top.drive_conn
    # if time() - t > cache_period:
    #     top.drive_conn = time(), get_drive_conn()
    #
    # return top.drive_conn[1]
    return get_drive_conn()


def get_spreadsheet_fromsvc(name, cache_period=120):
    return _get_spreadsheet(name, cache_period, gc=None)

def get_spreadsheet_fromusr(name, gc, cache_period=120):
    return _get_spreadsheet(name, cache_period, gc)


def _get_spreadsheet(name, cache_period, gc=None):
    """Grabs and returns worksheet1 for given workbook name

    Caches workbook (connection) for cache_period

    :param gc: Must be provided when not using service credentials
        to fetch a resource. If this is None, service credentials
        will be used!
    """
    # Disable caching for the moment because if connections get
    #  killed and the old worksheet still uses it, CannotSendRequest happens

    # def get_sheet(top, gc):
    #     print("Fetching workbook {}...".format(name))
    #     gc = get_db() if gc is None else gc
    #     wks = gc.open(name).sheet1
    #     top.sheets[name] = (time(), wks)
    #
    # top = flask._app_ctx_stack
    # if not hasattr(top, 'sheets'):
    #     top.sheets = {}
    #
    # if name not in top.sheets:
    #     get_sheet(top, gc)
    # else:
    #     t, wks = top.sheets[name]
    #     if time() - t > cache_period:
    #         get_sheet(top, gc)
    #
    # return top.sheets[name][1]

    gc = get_db() if gc is None else gc
    wks = gc.open(name).sheet1
    return wks


class NonUniqueIndexError(Exception):
    pass


def sheet2dict(sheet, index_key, lower=True):
    d = {}
    rkeys = dict(enumerate(sheet.row_values(1)))
    keys = sheet.row_values(1)
    if index_key not in rkeys.values():
        raise KeyError("{} does not exist in {}".format(index_key, sheet.title))
    for entry in sheet.get_all_values()[1:]:
        pk_val = entry[[i for i,key in enumerate(keys) if key==index_key][0]]
        if pk_val in d:
            raise NonUniqueIndexError(pk_val)
        d[pk_val.lower() if lower else pk_val] = dict(zip(keys, entry))

    return d


def sheet2lod(sheet):
    keys = sheet.row_values(1)
    return [dict(zip(keys, entry)) for entry in sheet.get_all_values()[1:]]

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


def _wkskeys(wks):
    return {v: k for k, v in enumerate(wks.row_values(1))}


def _get_free_lockers():
    lockers = get_spreadsheet_fromsvc("Lockers", cache_period=120)
    lockers_keys = _wkskeys(lockers)
    locker_sales = get_spreadsheet_fromsvc("Locker_Rentals", cache_period=30)
    locker_sales_keys = _wkskeys(locker_sales)

    rentable = {entry[lockers_keys["Number"]] for entry in
                lockers.get_all_values()[1:]
                if entry[lockers_keys["Type"]] == "Rentable"}
    all_rentable = rentable.copy()
    used_locker_numbers = set()
    doubly_used = []
    invalid_entries = []

    for entry in locker_sales.get_all_values()[1:]:
        locker_number = entry[locker_sales_keys["Locker_Number"]]

        if locker_number in used_locker_numbers:
            doubly_used.append(entry)
        elif locker_number and locker_number not in all_rentable:
            invalid_entries.append(entry)

        if (
            locker_number in rentable and
            entry[locker_sales_keys["Locker_Number"]] != "Yes"
        ):
            rentable.remove(locker_number)
            used_locker_numbers.add(locker_number)

    res = list(sorted(rentable, key=lambda x: int(x)))
    res.extend([
        "<br><br>"
        "Doubly-used locker numbers",
    ])
    res.extend(doubly_used)
    res.extend([
        "<br><br>",
        "Invalid entries",
    ])
    res.extend(invalid_entries)
    return "\n<br>".join(map(str, res))


def _cache_free_lockers(cache_period=30):
    top = flask._app_ctx_stack
    if not hasattr(top, 'free_lockers'):
        top.free_lockers = None

    if top.free_lockers is None or time() - top.free_lockers[0] > cache_period:
        top.free_lockers = time(), _get_free_lockers()

    return top.free_lockers[1]


@app.route('/student/availablelockers')
def available_lockers():
    return _cache_free_lockers()


@app.route('/student/seattle/signup')
@authenticated(TYPE_USER)
def seattle_signup(credentials):
    oauth2_service = get_oauth2_service(credentials)
    google_email = oauth2_service.userinfo().get().execute()["email"]

    not_registered = _check_not_registered(google_email)
    if not_registered is not None:
        return not_registered

    FORM_URL = "https://docs.google.com/forms/d/" \
               "1dW7sqp0lc7nAGhpFYKki5KCqNYDSbEAdAe-0eMBd9eM/" \
               "viewform?entry.694442738={google_email}"
    return flask.redirect(FORM_URL.format(google_email=google_email))


def _check_not_registered(google_email):
    # Check if they're registered
    wks = get_spreadsheet_fromsvc("ECESS 2015W Student Contact Form (Responses)")
    keys = {v: k for k, v in enumerate(wks.row_values(1))}
    for entry in wks.get_all_values()[1:]:
        if entry[keys["Google_Email"]].lower() == google_email.lower():
            return None
    else:
        return "You don't seem to be in our database yet! Please visit " \
               "<a href=\"{0}\">{0}</a> to fill out your " \
               "contact information first.".format(
            flask.url_for("student_register", _external=True)
        )


@app.route('/student/rentalocker')
@authenticated(TYPE_USER)
def rentalocker(credentials):
    oauth2_service = get_oauth2_service(credentials)
    google_email = oauth2_service.userinfo().get().execute()["email"]

    not_registered = _check_not_registered(google_email)
    if not_registered is not None:
        return not_registered

    # Check if they have a locker sales entry
    wks = get_spreadsheet_fromsvc("[ECESS] MCLD Locker Rental 2015W1 (Responses)")
    locker_form_keys = {v: k for k, v in enumerate(wks.row_values(1))}
    for locker_form_entry in wks.get_all_values()[1:]:
        if locker_form_entry[locker_form_keys["Google_Email"]].lower() == google_email.lower():
            payment_type = locker_form_entry[locker_form_keys["Payment_Method"]]
            break
    else:
        FORM_URL = "https://docs.google.com/forms/d/" \
               "1ixLqNKOggJqdasJ1u5QgQQA9bpLXpKO8F9XIHDKwy-0/" \
               "viewform?entry.1882898146={google_email}"
        return flask.redirect(FORM_URL.format(google_email=google_email))

    # Present their status
    res = [
        "Your ID is {}".format(google_email),
        "",
        "Step 1 (Rental Request Form): Complete! We have received your form."
    ]
    wks = get_spreadsheet_fromsvc("Locker_Rentals")
    keys = {v: k for k, v in enumerate(wks.row_values(1))}
    for entry in wks.get_all_values()[1:]:
        if (
            entry[keys["Google_Email"]] == google_email and
            entry[keys["Term"]] == "2015W1"
        ):
            payment_status = entry[keys["Paid"]]
            if payment_status == "Not_Paid":
                if payment_type == "Cash":
                    res.append("Step 2 (Payment): Waiting for your payment; please"
                               " visit MCLD 434 to pay with cash! Cost is"
                               " $11.")
                elif payment_type == "PayPal_Invoice":
                    res.append("Step 2 (Payment): We need to send you a PayPal Invoice; "
                               "you should receive it soon so that you "
                               "are able to pay for your locker.")
            elif payment_status == "Invoice_Sent":
                res.append("Step 2 (Payment): A PayPal Invoice has been sent to your "
                           " email. Please promptly pay this invoice so that"
                           " we can assign you a locker number.")
            elif payment_status == "Payment_Received":
                res.append("Step 2 (Payment): We have successfully received your "
                           "payment!")
                locker_number = entry[keys["Locker_Number"]]
                if locker_number:
                    res.append("Step 3 (Locker Assignment): Your locker has been assigned. Your locker"
                               " is #{}".format(locker_number))
                else:
                    res.append("Step 3 (Locker Assignment): We have not yet determined your locker "
                               "number. Please check back in a bit!")

            return "\n<br>".join(res)
    else:
        res.append("Step 1a: We have received your locker rental request. If"
                   " there are any available lockers for you, we'll try "
                   "to process it as soon as possible!")
        return "\n<br>".join(res)


@app.route('/admin/invoicestosend')
@authenticated(TYPE_EDITOR)
def invoices_to_send(credentials):
    gc = get_drive_conn(credentials)
    try:
        locker_rentals = sheet2lod(get_spreadsheet_fromusr(
            "Locker_Rentals",
            gc=gc
        ))
        locker_form = sheet2dict(get_spreadsheet_fromusr(
            "[ECESS] MCLD Locker Rental 2015W1 (Responses)",
            gc=gc
        ), "Google_Email")
        contact_form = sheet2dict(get_spreadsheet_fromusr(
            "ECESS 2015W Student Contact Form (Responses)",
            gc=gc
        ), "Google_Email")
    except gspread.SpreadsheetNotFound:
        return "Unauthorized"  # TODO return a 401 here

    l = []
    for entry in locker_rentals:
        gmail = entry["Google_Email"]
        form_entry = locker_form.get(gmail)
        if form_entry is None:
            # l.append("Could not find {} in rental form responses.".format(gmail))
            continue
        payment_type = form_entry["Payment_Method"]
        if payment_type == "PayPal_Invoice" and entry["Paid"] == "Not_Paid":
            l.append(
                "Email_Address: {}, Google_Email: {}".format(
                    contact_form[gmail]["Email_Address"],
                    contact_form[gmail]["Google_Email"]
            ))

    return "\n<br>".join(l)


@app.route('/admin/lockerqueue')
@authenticated(TYPE_EDITOR)
def locker_queue(credentials):
    gc = get_drive_conn(credentials)
    try:
        _locker_rentals = sheet2lod(get_spreadsheet_fromusr(
            "Locker_Rentals",
            gc=gc
        ))
        locker_rentals = defaultdict(list)
        for lr in _locker_rentals:
            locker_rentals[lr["Google_Email"].lower()].append(lr)
        locker_form = sheet2lod(get_spreadsheet_fromusr(
            "[ECESS] MCLD Locker Rental 2015W1 (Responses)",
            gc=gc
        ))
        contact_form = sheet2dict(get_spreadsheet_fromusr(
            "ECESS 2015W Student Contact Form (Responses)",
            gc=gc
        ), "Google_Email")
    except gspread.SpreadsheetNotFound:
        return "Unauthorized"  # TODO return a 401 here

    d = {
        "pre_150_ece_renewal": [],
        "ece": [],
        "non_ece": [],
        "no_contact_email": [],
        "unpaid_over_4d_no_email": []
    }


    for i, entry in enumerate(locker_form):
        gmail = entry["Google_Email"].lower()
        try:
            email = contact_form[gmail]["Email_Address"]
        except KeyError:
            email = None
        dln = entry["Desired_Locker_Number"]
        # TODO XXX Handle multiple terms
        if gmail not in locker_rentals:
            contact_user = contact_form.get(gmail)
            if contact_user is None:
                d["no_contact_email"].append(gmail)
                continue
            if contact_form[gmail]["Dept"] == "ECE":
                if i < 150 and entry["Renewal"] == "Yes":
                    d["pre_150_ece_renewal"].append("{} {}".format(gmail, dln))
                else:
                    d["ece"].append(gmail)
            else:
                d["non_ece"].append(gmail)
        else:
            for lr_entry in locker_rentals[gmail]:
                if lr_entry["Warning_Email_Sent"] != "Yes" \
                        and lr_entry["Paid"] == "Not_Paid":
                    try:
                        parsed = arrow.get(entry["Timestamp"], "M/DD/YYYY HH:mm:ss")
                        diff = (arrow.utcnow() - parsed).days
                        print(diff)
                        if diff >= 4:
                            d["unpaid_over_4d_no_email"]\
                                .append("{}".format(email))
                    except arrow.parser.ParserError as e:
                        print("{}: {}".format(entry["Timestamp"], e))

    l = []
    l.append("<br><br>== Pre-150 ECE Renewals ==<br>")
    l.extend(d["pre_150_ece_renewal"])
    l.append("<br><br>== ECE students ==<br>")
    l.extend(d["ece"])
    l.append("<br><br>== Non-ECE Students ==<br>")
    l.extend(d["non_ece"])
    l.append("<br><br>== These students' Google_Emails are not on the Contact sheet, i.e., the"
             "y have not filled out the Contact form ==<br>")
    l.extend(d["no_contact_email"])
    l.append("<br><br>== Warning Emails to send ==<br>")
    l.extend(d["unpaid_over_4d_no_email"])

    return "\n<br>".join(l)


@app.route("/admin/lockertenants")
@authenticated(TYPE_EDITOR)
def locker_tenants(credentials):
    gc = get_drive_conn(credentials)
    try:
        _locker_rentals = sheet2lod(get_spreadsheet_fromusr(
            "Locker_Rentals",
            gc=gc
        ))
        contact_form = sheet2dict(get_spreadsheet_fromusr(
            "ECESS 2015W Student Contact Form (Responses)",
            gc=gc
        ), "Google_Email")
    except gspread.SpreadsheetNotFound:
        return "Unauthorized"  # TODO

    l = []
    for entry in _locker_rentals:
        locker_number = entry["Locker_Number"]
        try:
            legal_name = contact_form[entry["Google_Email"]]["Full_Legal_Name"]
        except KeyError:
            continue
        if locker_number:
            l.append(
                "{}    {}".format(str(locker_number).zfill(3),
                               legal_name)
            )

    return "\n<br>".join(sorted(l))


@app.route('/oauth2callback')
def oauth2callback():
    usertypes = flask.session[SessKeys.usertypes]
    scopes = [scope for usertype, scopes in SCOPES.items()
              for scope in scopes if usertype in usertypes]
    scope_urls = ['https://www.googleapis.com/auth/{}'.format(scope)
                  if not scope.startswith("http") else scope
                  for scope in scopes]
    print("Authenticating with scopes: {}".format(scope_urls))
    flow = client.OAuth2WebServerFlow(
        client_id=APP_CLIENT_ID,
        client_secret=APP_CLIENT_SECRET,
        scope=scope_urls,
        redirect_uri=flask.url_for('oauth2callback', _external=True)
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
    app.config["PROPAGATE_EXCEPTIONS"] = True
    app.wsgi_app = ReverseProxied(app.wsgi_app)
    app.run(threaded=True)
