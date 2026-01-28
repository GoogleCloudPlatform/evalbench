import datetime
import json

import google.auth
from google.cloud import iam_credentials_v1


def generate_jwt_payload(service_account_email: str, resource_url: str) -> str:
    """Generates JWT payload for service account.

    Creates a properly formatted JWT payload with standard claims (iss, sub,
    aud, iat, exp) needed for IAP authentication.

    Args:
        service_account_email (str): Specifies the service account that the
        JWT is created for.
        resource_url (str): Specifies the scope of the JWT, the URL that the
        JWT will be allowed to access.

    Returns:
        str: JSON string containing the JWT payload with properly formatted
        claims.
    """
    # Create current time and expiration time (1 hour later) in UTC
    iat = datetime.datetime.now(tz=datetime.timezone.utc)
    exp = iat + datetime.timedelta(seconds=3600)

    # Convert datetime objects to numeric timestamps (seconds since epoch)
    # as required by JWT standard (RFC 7519)
    payload = {
        "iss": service_account_email,
        "sub": service_account_email,
        "aud": resource_url,
        "iat": int(iat.timestamp()),
        "exp": int(exp.timestamp()),
    }

    return json.dumps(payload)


def sign_jwt(target_sa: str, resource_url: str) -> str:
    """Signs JWT payload using ADC and IAM credentials API.

    Uses Google Cloud's IAM Credentials API to sign a JWT. This requires the
    caller to have iap.webServiceVersions.accessViaIap permission on the
    target service account.

    Args:
        target_sa (str): Service Account JWT is being created for.
            iap.webServiceVersions.accessViaIap permission is required.
        resource_url (str): Audience of the JWT and scope of the JWT token.
            This is the URL of the IAP-secured application.

    Returns:
        str: A signed JWT that can be used to access IAP-secured applications.
            Use in Authorization header as: 'Bearer <signed_jwt>'
    """
    # Get default credentials from environment or application credentials
    source_credentials, project_id = google.auth.default()

    # Initialize IAM credentials client with source credentials
    iam_client = iam_credentials_v1.IAMCredentialsClient(credentials=source_credentials)

    # Generate the service account resource name.
    # Project should always be "-".
    # Replacing the wildcard character with a project ID is invalid.
    name = iam_client.service_account_path("-", target_sa)

    # Create and sign the JWT payload
    payload = generate_jwt_payload(target_sa, resource_url)

    # Sign the JWT using the IAM credentials API
    response = iam_client.sign_jwt(name=name, payload=payload)

    return response.signed_jwt
