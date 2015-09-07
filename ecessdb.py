from six.moves import input

import os
from getpass import getpass

import keyring


ECESSDB_USER_ENV_VAR = "ECESSDB_USER"
SERVICE_NAME = "ecessdb"


def get_credentials():
    username = os.getenv(ECESSDB_USER_ENV_VAR) or input("Username: ")
    os.environ[ECESSDB_USER_ENV_VAR] = username
    password = keyring.get_password(SERVICE_NAME, username)
    if password is None:
        password = getpass()
        keyring.set_password(SERVICE_NAME, username, password)

    return username, password


def delete_credentials():
    username = os.getenv(ECESSDB_USER_ENV_VAR) or input("Username: ")
    del os.environ[ECESSDB_USER_ENV_VAR]
    keyring.delete_password(SERVICE_NAME, username)


