# Changelog

## [1.1.0](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.0.0...v1.1.0) (2026-03-20)


### Features

* Add a Gemini-powered dataset translation tool. ([#257](https://github.com/GoogleCloudPlatform/evalbench/issues/257)) ([a5c0359](https://github.com/GoogleCloudPlatform/evalbench/commit/a5c03596d851becbd82bb89b65399580bdd738d9))
* Add Cloud Run support and make the server port configurable via… ([#234](https://github.com/GoogleCloudPlatform/evalbench/issues/234)) ([34110b1](https://github.com/GoogleCloudPlatform/evalbench/commit/34110b1266709a667bb3aca3be5a514b51262cfe))
* add evalbench release pipeline and bundling ([#276](https://github.com/GoogleCloudPlatform/evalbench/issues/276)) ([a68b348](https://github.com/GoogleCloudPlatform/evalbench/commit/a68b348a6d548854dc693ad596f276c8fa24091a))
* Add Gemini 3.0 Pro and 3.1 Pro preview model configurations ([f8f036c](https://github.com/GoogleCloudPlatform/evalbench/commit/f8f036cb441ebf5cc3562c2484243dfbb0b347e8))
* add QueryData API generator and refactor SQLGenWork ([#281](https://github.com/GoogleCloudPlatform/evalbench/issues/281)) ([44d07dc](https://github.com/GoogleCloudPlatform/evalbench/commit/44d07dc245307b67f83912737accda47024c826a))
* Add remote MCP server connectivity verification ([7bf5716](https://github.com/GoogleCloudPlatform/evalbench/commit/7bf57162671bd1c1dec1ade4a6ceb1aea4ef95fc))
* Add remote MCP server connectivity verification ([a64aa37](https://github.com/GoogleCloudPlatform/evalbench/commit/a64aa37ec1bc3facea65a3880333aeb75625135b))
* Add support for syncing Gemini CLI skills to fake home ([7e2265b](https://github.com/GoogleCloudPlatform/evalbench/commit/7e2265b51d3e51411b6812c24b5f89975b4a7fbe))
* Configure a dedicated home directory and user for evalbench within the Docker container. ([89238f5](https://github.com/GoogleCloudPlatform/evalbench/commit/89238f5cc6a1db6cdd55c519d83b714efd0bbd7f))
* Configure GCS FUSE for session management and expose new ports for UI and metrics. ([b02489e](https://github.com/GoogleCloudPlatform/evalbench/commit/b02489ec731a618e7d74a0fffce4d4f55d624c13))
* Enable session-specific fake home directories for Gemini CLI and improve JSON parsing, while passing the session ID to the generator configuration. ([0e0c06b](https://github.com/GoogleCloudPlatform/evalbench/commit/0e0c06be8c53ad9496742d0ec28a85a6d4829506))
* Enhance Evalbench Viewer UI ([#252](https://github.com/GoogleCloudPlatform/evalbench/issues/252)) ([e3a2f95](https://github.com/GoogleCloudPlatform/evalbench/commit/e3a2f95d5ea0e8656a3291dc5333865a058f0999))
* Enhance results directory discovery in the viewer and ensure the CSV reporter outputs to a shared volume when running in server mode. ([a4761e1](https://github.com/GoogleCloudPlatform/evalbench/commit/a4761e1b0f71dc3277b9fad18751f828fa7087a6))
* Install Node.js via NodeSource PPA, consolidating package installations and removing NVM. ([a9f2741](https://github.com/GoogleCloudPlatform/evalbench/commit/a9f2741edeacde54ef7fc5c45befdaef2a406edd))
* Introduce Horizontal Pod Autoscaler, offload blocking evaluatio… ([#269](https://github.com/GoogleCloudPlatform/evalbench/issues/269)) ([a639282](https://github.com/GoogleCloudPlatform/evalbench/commit/a6392823a8be9d8e0b3be02b41a5f641b65c7a5a))
* Introduce Horizontal Pod Autoscaler, offload blocking evaluation tasks to a thread pool, and enhance session manager robustness. ([6024fb3](https://github.com/GoogleCloudPlatform/evalbench/commit/6024fb327b5f92e20991aa2236e2d39c75414c27))
* Multi run orchestrator ([#258](https://github.com/GoogleCloudPlatform/evalbench/issues/258)) ([aec92c9](https://github.com/GoogleCloudPlatform/evalbench/commit/aec92c9f3a185aaeffb883c7836c109d833be9c5))
* Schema, Database Instantiation ([#259](https://github.com/GoogleCloudPlatform/evalbench/issues/259)) ([dcb8bf6](https://github.com/GoogleCloudPlatform/evalbench/commit/dcb8bf64e1f823b5701d99205bc7a92aadd467c8))
* **spanner:** Improve and extend support for Spanner Client ([#247](https://github.com/GoogleCloudPlatform/evalbench/issues/247)) ([ac6625a](https://github.com/GoogleCloudPlatform/evalbench/commit/ac6625af550d2b9475f3a53fc5c36a6bfc97b3e1))
* Sync Gemini CLI skills into fake_home ([93e6265](https://github.com/GoogleCloudPlatform/evalbench/commit/93e6265f65c9b4e7d26242e2f257e1d4b0fdb7e8))


### Bug Fixes

* Configure absl.logging to output to stdout and initialize its handler. ([560d0ee](https://github.com/GoogleCloudPlatform/evalbench/commit/560d0ee79a3f6a22702295aa27be7312d40fca24))
* Correct Gemini CLI response parsing to strip markdown code blocks and remove a redundant prompt argument, and update Makefile container names, pre-run cleanup, and volume mount paths. ([#275](https://github.com/GoogleCloudPlatform/evalbench/issues/275)) ([daa0821](https://github.com/GoogleCloudPlatform/evalbench/commit/daa08214a2cfcabf2d4a074c5329e860b2063377))
* **dataset:** preserve multi-dialect golden_sql for BIRD ([#262](https://github.com/GoogleCloudPlatform/evalbench/issues/262)) ([12ccf98](https://github.com/GoogleCloudPlatform/evalbench/commit/12ccf98e95f046c811dbf3fd96d6d50ba25594f4))
* handle empty MySQL passwords and add Cloud SQL support to ensure_database_exists ([#268](https://github.com/GoogleCloudPlatform/evalbench/issues/268)) ([beef7ec](https://github.com/GoogleCloudPlatform/evalbench/commit/beef7ec3094fa5e89eb79c3851e5bd3c0d7f9e0e))
* implement timeouts to prevent thread hanging in evaluator ([#266](https://github.com/GoogleCloudPlatform/evalbench/issues/266)) ([bb77c2f](https://github.com/GoogleCloudPlatform/evalbench/commit/bb77c2fb30e6164fbd1e577cfbfad4fc8f3d2fa1))
* prevent execution thread deadlocks and db connection leaks ([#267](https://github.com/GoogleCloudPlatform/evalbench/issues/267)) ([265fee8](https://github.com/GoogleCloudPlatform/evalbench/commit/265fee875710c474982eda130b40c894c55cbf75))
* Prevent logging handler from closing sys.stdout by wrapping it in an `UncloseableStream`. ([d7c453e](https://github.com/GoogleCloudPlatform/evalbench/commit/d7c453ed40b864f395f13cc49899c3f6bffee4c2))
* various improvements, fixes to the SpannerDB driver ([#264](https://github.com/GoogleCloudPlatform/evalbench/issues/264)) ([5c6f425](https://github.com/GoogleCloudPlatform/evalbench/commit/5c6f425cdb407f8f792426c7d2f222ffb525452f))
