import unittest
from unittest.mock import patch, MagicMock
from generators.models.query_data_api import QueryDataAPIGenerator
from google.api_core.exceptions import (
    ResourceExhausted,
    ServiceUnavailable,
    DeadlineExceeded,
)
from util.rate_limit import ResourceExhaustedError


class TestQueryDataAPIGenerator(unittest.TestCase):

    @patch('generators.models.query_data_api.gda')
    def test_init_sets_properties(self, mock_gda):
        mock_gda.DataChatServiceClient = MagicMock()
        config = {
            "project_id": "test-project",
            "location": "us-central1",
            "context": {"key": "value"}
        }
        generator = QueryDataAPIGenerator(config)
        self.assertEqual(generator.project_id, "test-project")
        self.assertEqual(generator.location, "us-central1")
        self.assertEqual(generator.context, {"key": "value"})
        self.assertEqual(
            generator.api_endpoint, "geminidataanalytics.googleapis.com"
        )
        self.assertEqual(generator.name, "query_data_api")
        mock_gda.DataChatServiceClient.assert_called_once()

    @patch('generators.models.query_data_api.gda')
    def test_generate_internal_success(self, mock_gda):
        mock_client_instance = MagicMock()
        mock_gda.DataChatServiceClient.return_value = mock_client_instance

        mock_response = MagicMock()
        mock_response.generated_query = "SELECT * FROM test;"
        mock_response.intent_explanation = "Selects all from test"
        mock_response.disambiguation_questions = ["Did you mean table A?"]
        mock_client_instance.query_data.return_value = mock_response

        config = {
            "project_id": "test-project",
            "location": "us-central1"
        }
        generator = QueryDataAPIGenerator(config)

        result = generator.generate_internal("What is in test?")

        self.assertEqual(result["generated_sql"], "SELECT * FROM test;")
        self.assertEqual(
            result["other"]["intent_explanation"],
            "Selects all from test")
        self.assertEqual(
            result["other"]["disambiguation_question"], [
                "Did you mean table A?"])

        mock_client_instance.query_data.assert_called_once()

    @patch('generators.models.query_data_api.gda')
    def test_generate_internal_exception(self, mock_gda):
        mock_client_instance = MagicMock()
        mock_gda.DataChatServiceClient.return_value = mock_client_instance
        mock_client_instance.query_data.side_effect = Exception("API error")

        config = {
            "project_id": "test-project"
        }
        generator = QueryDataAPIGenerator(config)

        with self.assertRaises(Exception) as context:
            generator.generate_internal("What is in test?")

        self.assertIn("API error", str(context.exception))

    @patch('generators.models.query_data_api.gda')
    def test_generate_internal_resource_exhausted(self, mock_gda):
        mock_client_instance = MagicMock()
        mock_gda.DataChatServiceClient.return_value = mock_client_instance
        mock_client_instance.query_data.side_effect = (
            ResourceExhausted("Quota exceeded")
        )

        config = {
            "project_id": "test-project"
        }
        generator = QueryDataAPIGenerator(config)

        with self.assertRaises(ResourceExhaustedError):
            generator.generate_internal("What is in test?")

    @patch('generators.models.query_data_api.gda')
    def test_generate_internal_service_unavailable(self, mock_gda):
        mock_client_instance = MagicMock()
        mock_gda.DataChatServiceClient.return_value = mock_client_instance
        mock_client_instance.query_data.side_effect = (
            ServiceUnavailable("Service unavailable")
        )

        config = {
            "project_id": "test-project"
        }
        generator = QueryDataAPIGenerator(config)

        with self.assertRaises(ResourceExhaustedError):
            generator.generate_internal("What is in test?")

    @patch('generators.models.query_data_api.gda')
    def test_generate_internal_deadline_exceeded(self, mock_gda):
        mock_client_instance = MagicMock()
        mock_gda.DataChatServiceClient.return_value = mock_client_instance
        mock_client_instance.query_data.side_effect = (
            DeadlineExceeded("Deadline exceeded")
        )

        config = {
            "project_id": "test-project"
        }
        generator = QueryDataAPIGenerator(config)

        with self.assertRaises(ResourceExhaustedError):
            generator.generate_internal("What is in test?")

    @patch('generators.models.query_data_api.requests')
    @patch('generators.models.query_data_api.google.auth.default')
    @patch('generators.models.query_data_api.gda')
    def test_generate_internal_rest_api_when_use_rest_api_true(
        self, mock_gda, mock_auth_default, mock_requests
    ):
        mock_credentials = MagicMock()
        mock_auth_default.return_value = (mock_credentials, "project-id")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "generatedQuery": "SELECT * FROM test_table",
            "intentExplanation": "Unreleased field query",
            "disambiguationQuestion": []
        }
        mock_requests.post.return_value = mock_resp

        config = {
            "project_id": "test-project",
            "location": "us-east1",
            "use_rest_api": True,
            "context": {"fake_unreleased_field": "dummy_value"}
        }
        generator = QueryDataAPIGenerator(config)
        result = generator.generate_internal("Run query with fake field")

        expected_query = "SELECT * FROM test_table"
        self.assertEqual(result["generated_sql"], expected_query)
        self.assertEqual(
            result["other"]["intent_explanation"],
            "Unreleased field query"
        )
        mock_requests.post.assert_called_once()

    @patch('generators.models.query_data_api.requests')
    @patch('generators.models.query_data_api.google.auth.default')
    @patch('generators.models.query_data_api.gda')
    def test_generate_internal_rest_fallback_arbitrary_nested_context(
        self, mock_gda, mock_auth_default, mock_requests
    ):
        mock_gda.QueryDataContext.side_effect = TypeError(
            "Proto type mismatch"
        )
        mock_credentials = MagicMock()
        mock_auth_default.return_value = (mock_credentials, "project-id")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "generatedQuery": "SELECT * FROM nosql_collection",
            "intentExplanation": "Arbitrary nesting query",
            "disambiguationQuestion": []
        }
        mock_requests.post.return_value = mock_resp

        nested_context = {
            "datasource_references": {
                "nosql_reference": {
                    "collection": {
                        "deep_levels": {
                            "metadata": ["tag1", "tag2"],
                            "flags": {"is_active": True}
                        }
                    }
                }
            }
        }
        config = {
            "project_id": "test-project",
            "location": "us-east1",
            "use_rest_api": True,
            "context": nested_context
        }
        generator = QueryDataAPIGenerator(config)
        result = generator.generate_internal("Query nested NoSQL")

        self.assertEqual(
            result["generated_sql"], "SELECT * FROM nosql_collection"
        )
        mock_requests.post.assert_called_once()
        call_kwargs = mock_requests.post.call_args[1]
        self.assertEqual(call_kwargs["json"]["context"], nested_context)


if __name__ == "__main__":
    unittest.main()
