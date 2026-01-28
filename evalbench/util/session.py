import asyncio
from google.adk.sessions import VertexAiSessionService
from google.genai import types


class EvalAgentEngineSessionMgr:
    def __init__(self, config):
        self.project_id = config.get("project_id")
        self.location = config.get("location")
        self.agent_engine_id = config.get("agent_engine_id")
        self.session_service = VertexAiSessionService(
            project=self.project_id,
            location=self.location,
            agent_engine_id=self.agent_engine_id,
        )

    def create_session(self, app_name: str, user_id: str):
        return asyncio.run(
            self.session_service.create_session(
                app_name=app_name, user_id=user_id, ttl=f"{24 * 60 * 60 * 10}s"
            )
        )

    def delete_session(self, app_name: str, user_id: str, session_id: str):
        asyncio.run(
            self.session_service.delete_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
        )

    def list_sessions(self, app_name: str, user_id: str):
        return asyncio.run(
            self.session_service.list_sessions(app_name=app_name, user_id=user_id)
        )
