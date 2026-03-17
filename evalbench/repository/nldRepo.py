import os
import shutil
import logging
import uuid
from git import Repo
from .base import Repository


class NLDRepo(Repository):
    def __init__(self, repo_config):
        self.config = repo_config

    def clone(self):
        if "repo_dir" not in self.config or "repo_url" not in self.config:
            return
        repo_dir = self.config["repo_dir"]
        repo_url = self.config["repo_url"]
        self.clone_from_dir(repo_dir, repo_url)

    def clone_dataset(self):
        job_id = f"{uuid.uuid4()}"
        if "repo_url" not in self.config:
            return
        repo_dir = os.path.join("/evalbench/datasets/nl2code", job_id)
        repo_url = self.config["repo_url"]
        self.clone_from_dir(repo_dir, repo_url)
        return job_id

    def cloneApplication(self, application_url, job_id):
        application_dir = os.path.join("/app", job_id)
        self.clone_from_dir(application_dir, application_url)

    def clone_from_dir(self, repo_dir, repo_url):
        if os.path.exists(repo_dir):
            logging.info(
                f"Repository directory '{repo_dir}' exists. Deleting it...")
            shutil.rmtree(repo_dir)
        logging.info(f"Cloning '{repo_url}' to '{repo_dir}'...")
        Repo.clone_from(repo_url, repo_dir)
