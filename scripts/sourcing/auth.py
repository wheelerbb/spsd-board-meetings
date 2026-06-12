import os
import google.auth

_SCOPES = [
    'https://www.googleapis.com/auth/cloud-platform',
    'https://www.googleapis.com/auth/drive.readonly',
]

def get_credentials():
    """Returns (credentials, project_id) using Application Default Credentials."""
    project_id = os.getenv('GCP_PROJECT_ID', 'spsd-board-meetings')
    credentials, _ = google.auth.default(scopes=_SCOPES)
    return credentials, project_id
