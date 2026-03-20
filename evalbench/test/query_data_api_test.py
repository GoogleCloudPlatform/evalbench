import unittest
from unittest.mock import patch, MagicMock
from generators.models.query_data_api import QueryDataAPIGenerator


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


if __name__ == "__main__":
    unittest.main()
