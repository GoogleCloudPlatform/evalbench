import os


def get_gcp_project(gcp_project_in_config_value: str | None):
    gcp_project_from_env = os.environ.get("EVAL_GCP_PROJECT_ID")
    if gcp_project_in_config_value is not None and gcp_project_in_config_value != "":
        return gcp_project_in_config_value
    elif gcp_project_from_env is not None:
        return gcp_project_from_env
    else:
        raise ValueError("No GCP project_id found in config or environment variables.")


def get_gcp_region(gcp_region_in_config_value: str | None):
    gcp_region_from_env = os.environ.get("EVAL_GCP_PROJECT_REGION")

    if gcp_region_in_config_value is not None and gcp_region_in_config_value != "":
        return gcp_region_in_config_value
    elif gcp_region_from_env is not None:
        return gcp_region_from_env
    else:
        raise ValueError("No GCP region found in config or environment variables.")
