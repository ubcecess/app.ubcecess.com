import json

import gspread
from oauth2client.client import SignedJwtAssertionCredentials

from ecessprivate.ecessdb import SERVICE_CREDENTIALS


def get_drive_conn(credentials=None):
    SCOPE = ['https://spreadsheets.google.com/feeds']
    if credentials is None:
        credentials = SignedJwtAssertionCredentials(
            SERVICE_CREDENTIALS['client_email'],
            SERVICE_CREDENTIALS['private_key'],
            SCOPE
        )
    gc = gspread.authorize(credentials)
    return gc
