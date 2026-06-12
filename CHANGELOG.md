# Changelog

## [1.9.0](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.8.0...v1.9.0) (2026-06-11)


### Features

* add AgyCliGenerator support to evaluator and models, including test suite and configuration datasets ([9e6d71f](https://github.com/GoogleCloudPlatform/evalbench/commit/9e6d71fb88fd652acb3d1e1ff2109b8a7e92f3bc))
* add AgyCliGenerator support to evaluator and models, including test suite and configuration datasets ([9e6d71f](https://github.com/GoogleCloudPlatform/evalbench/commit/9e6d71fb88fd652acb3d1e1ff2109b8a7e92f3bc))
* add Antigravity agent tab and update CLI version retrieval logic ([fe1a0ae](https://github.com/GoogleCloudPlatform/evalbench/commit/fe1a0ae2415a87bd090d18c0cde5548030c9e439))
* add Antigravity agent tab and update CLI version retrieval logic ([147680b](https://github.com/GoogleCloudPlatform/evalbench/commit/147680b34e375aa9c2d33e7cf6a8d1e197ce0553))
* **dea:** support YAML-only configuration and penguins dataset for DEA conversational evaluation ([#410](https://github.com/GoogleCloudPlatform/evalbench/issues/410)) ([2e37c1c](https://github.com/GoogleCloudPlatform/evalbench/commit/2e37c1ca804fe51a21d824892c9c6dd67821c406))
* recover resolved model label from agy cli logs for statistics bucket tagging ([ee7635f](https://github.com/GoogleCloudPlatform/evalbench/commit/ee7635f8d6f89647cef9bc66b79f637c83c30b2d))
* update MCP config translation to support both serverUrl and url fields in agy cli ([685b1b2](https://github.com/GoogleCloudPlatform/evalbench/commit/685b1b2c6b2699a6b87749e03a55b60e0ff0d7ed))


### Bug Fixes

* **dea:** resolve concurrency deadlock in GcpAdcCredentialService ([#422](https://github.com/GoogleCloudPlatform/evalbench/issues/422)) ([6619ae5](https://github.com/GoogleCloudPlatform/evalbench/commit/6619ae50d18c5a2d096ad4c2293dd9502683dcda))
* pin pyopenssl&lt;26.2 to avoid google-auth mTLS regression ([#424](https://github.com/GoogleCloudPlatform/evalbench/issues/424)) ([c8916ae](https://github.com/GoogleCloudPlatform/evalbench/commit/c8916aeac729d2cf5da81838001195ec04a05d2a))
* update error message to list supported AgentCliGenerator subclasses ([dfe1e8e](https://github.com/GoogleCloudPlatform/evalbench/commit/dfe1e8e27063208f710666f3634624050a527813))
* update generator key path in trends data mapping to model_config.generator ([3739f9d](https://github.com/GoogleCloudPlatform/evalbench/commit/3739f9dd3dd7d5f8b5038f8b51b2bceb396e7a06))

## [1.8.0](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.7.1...v1.8.0) (2026-06-03)


### Features

* add Codex to agent filters in viewer ([a2be23c](https://github.com/GoogleCloudPlatform/evalbench/commit/a2be23caccb27a66d7a638063fc086445771a80b))
* add Codex to agent filters in viewer ([#387](https://github.com/GoogleCloudPlatform/evalbench/issues/387)) ([2ac6c1f](https://github.com/GoogleCloudPlatform/evalbench/commit/2ac6c1f7cb493e919c97520736e34ad59ba61d3f))
* add filter_native_tools option to trajectory_matcher to optionally ignore native harness tools during scoring ([2c34d94](https://github.com/GoogleCloudPlatform/evalbench/commit/2c34d94e13d71b2e491009f4858108388ea040de))
* add packaged console script entrypoint to support `uvx` execution ([#385](https://github.com/GoogleCloudPlatform/evalbench/issues/385)) ([8ea07f8](https://github.com/GoogleCloudPlatform/evalbench/commit/8ea07f80fa8b58184b8677480dc886470c1e0662))
* **dea:** define EvalDeaRequest input model for conversational evaluations ([#407](https://github.com/GoogleCloudPlatform/evalbench/issues/407)) ([04b91cf](https://github.com/GoogleCloudPlatform/evalbench/commit/04b91cfafa9c049122b5530e89f790572f479058))
* opt-in function-calling for the Gemini SDK judge ([#409](https://github.com/GoogleCloudPlatform/evalbench/issues/409)) ([d97f511](https://github.com/GoogleCloudPlatform/evalbench/commit/d97f511770941c7cf1acf975ec38b52b5030f51b))
* Rename package to google-evalbench and decouple viewer dependencies ([#390](https://github.com/GoogleCloudPlatform/evalbench/issues/390)) ([0d75811](https://github.com/GoogleCloudPlatform/evalbench/commit/0d758112ae9f6618a7c8058f032f44a440a381f8))
* **scorers:** filter native tools in trajectory_matcher with opt-out flag ([2c1ab58](https://github.com/GoogleCloudPlatform/evalbench/commit/2c1ab58dee8e52ebdc8b39751032ca6ade0000d2))
* stabilize Cloud Run deployment and polish standalone CLI UX ([#389](https://github.com/GoogleCloudPlatform/evalbench/issues/389)) ([4720eef](https://github.com/GoogleCloudPlatform/evalbench/commit/4720eefbebed97d9f21a01a6d4541d77267b443c))
* support work_dir for claude code eval ([#403](https://github.com/GoogleCloudPlatform/evalbench/issues/403)) ([179e0d3](https://github.com/GoogleCloudPlatform/evalbench/commit/179e0d3f41584112e686675b68ee37a52bea3492))


### Bug Fixes

* add --no-sync flag to runtime uv run commands to prevent PyPI timeouts ([#392](https://github.com/GoogleCloudPlatform/evalbench/issues/392)) ([0c4783c](https://github.com/GoogleCloudPlatform/evalbench/commit/0c4783c2ca3d1c6a62d844faf9f359119c9dd6f1))
* allow-list files in fake home directory for Gemini CLI ([#395](https://github.com/GoogleCloudPlatform/evalbench/issues/395)) ([734bc2a](https://github.com/GoogleCloudPlatform/evalbench/commit/734bc2a154a264e4066d4e826d51c44bcbc1c603))
* fix Mesop event routing bug in trends dropdown ([023d150](https://github.com/GoogleCloudPlatform/evalbench/commit/023d1505a73e77835f75746dfc5624a921ce2832))
* **gemini-cli:** support 'name' parameter key in skill extraction ([#378](https://github.com/GoogleCloudPlatform/evalbench/issues/378)) ([62400da](https://github.com/GoogleCloudPlatform/evalbench/commit/62400daf314926a0cd7bfd916997e9203e91f8b5))
* patch absl help output when running via uvx/launcher ([85048e2](https://github.com/GoogleCloudPlatform/evalbench/commit/85048e2760d7951351423facbf0c6fa2106b439e))
* prevent silent errors on DB query timeouts and extend deadline ([#406](https://github.com/GoogleCloudPlatform/evalbench/issues/406)) ([fbbd31d](https://github.com/GoogleCloudPlatform/evalbench/commit/fbbd31d42a3af782411ae1e0c131f4ef2beef143))
* surface eval failures instead of silently terminating or crashing ([#398](https://github.com/GoogleCloudPlatform/evalbench/issues/398)) ([9c36108](https://github.com/GoogleCloudPlatform/evalbench/commit/9c361083f53ae3b8ae622aafedd7d483701ca413))

## [1.7.1](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.7.0...v1.7.1) (2026-05-07)


### Bug Fixes

* trigger release-please for username/password issue ([#371](https://github.com/GoogleCloudPlatform/evalbench/issues/371)) ([506a717](https://github.com/GoogleCloudPlatform/evalbench/commit/506a71712062ded66108900460cbb73afc2a9a0c))

## [1.7.0](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.6.0...v1.7.0) (2026-05-05)


### Features

* add Dataform scorers and plumb isolated fake_home workspace directory tracking ([#349](https://github.com/GoogleCloudPlatform/evalbench/issues/349)) ([b4ddfda](https://github.com/GoogleCloudPlatform/evalbench/commit/b4ddfda9241eae431ea143ea87c5cb8a99cea989))
* Add dbt Scorers for Agent Evaluations ([#367](https://github.com/GoogleCloudPlatform/evalbench/issues/367)) ([5496d59](https://github.com/GoogleCloudPlatform/evalbench/commit/5496d59948b6e6f86322c93e25aecc664ed5e73c))
* Add GCS Artifacts Reporter for Agent Evaluations ([#366](https://github.com/GoogleCloudPlatform/evalbench/issues/366)) ([11def06](https://github.com/GoogleCloudPlatform/evalbench/commit/11def06bc3f7a68acf17c890127dda13c0ebe50c))
* implement lifecycle execution for setup and teardown scripts ([#360](https://github.com/GoogleCloudPlatform/evalbench/issues/360)) ([9de38da](https://github.com/GoogleCloudPlatform/evalbench/commit/9de38da0fca549b8e160044efe6891e630607450))


### Bug Fixes

* correct indentation in eval_service.py to resolve SyntaxError ([8512441](https://github.com/GoogleCloudPlatform/evalbench/commit/851244137ddc01271216cfe44c5bc090e0ab5bac))
* correct return signature in base Orchestrator.process() ([ad8228e](https://github.com/GoogleCloudPlatform/evalbench/commit/ad8228e0db3bea785b030da354020ebc8e6b83df))
* correct return signature in remaining Orchestrators ([b78a9e9](https://github.com/GoogleCloudPlatform/evalbench/commit/b78a9e9759b364755b26898e459a1821cdae1b6f))
* pass None for missing metrics parameter in AgentOrchestrator results initialization ([c72c758](https://github.com/GoogleCloudPlatform/evalbench/commit/c72c7580dedc3fdfe82121d229c2582680d13a4e))

## [1.6.0](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.5.0...v1.6.0) (2026-04-30)


### Features

* add exponential backoff for Gemini 429 resource exhausted errors ([#352](https://github.com/GoogleCloudPlatform/evalbench/issues/352)) ([11d4236](https://github.com/GoogleCloudPlatform/evalbench/commit/11d423658a0b79385e5e05644cb614b32674b0dd))
* allow execution of mixed DDL and DML statements in Spanner driver ([#351](https://github.com/GoogleCloudPlatform/evalbench/issues/351)) ([64c0b63](https://github.com/GoogleCloudPlatform/evalbench/commit/64c0b630ac95e9ad372afa516d6dc7b34be87ec9))
* chunk Spanner DDL statements into groups of 10 to avoid limits ([#353](https://github.com/GoogleCloudPlatform/evalbench/issues/353)) ([306caad](https://github.com/GoogleCloudPlatform/evalbench/commit/306caad98bb5ee6825d834441edbf3a95067b030))
* handle quoted fields in CSV setup data ([#350](https://github.com/GoogleCloudPlatform/evalbench/issues/350)) ([3ba2334](https://github.com/GoogleCloudPlatform/evalbench/commit/3ba2334d5a816fb4d4614a814e9c8a0a53ab1059))
* handle recursive dependency cleanup in Spanner table drop ([#356](https://github.com/GoogleCloudPlatform/evalbench/issues/356)) ([3eb4d45](https://github.com/GoogleCloudPlatform/evalbench/commit/3eb4d45c2ecbc8bda538f5df7748f32e77ca0b17))
* use parameterized queries for data insertion in MySQL, Postgres, and SQLite ([#358](https://github.com/GoogleCloudPlatform/evalbench/issues/358)) ([733724d](https://github.com/GoogleCloudPlatform/evalbench/commit/733724d9978b73c274dd0a489b1c35cd78c2cb75))


### Bug Fixes

* ensure setup and cleanup sql run for ddl and dml queries ([#354](https://github.com/GoogleCloudPlatform/evalbench/issues/354)) ([89b7c4d](https://github.com/GoogleCloudPlatform/evalbench/commit/89b7c4d438680cf3c96d6399d72fc46ff1ad6bdd))

## [1.5.0](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.4.0...v1.5.0) (2026-04-26)


### Features

* add support for custom host configuration and insecure gRPC channels via environment variables ([#336](https://github.com/GoogleCloudPlatform/evalbench/issues/336)) ([05efee0](https://github.com/GoogleCloudPlatform/evalbench/commit/05efee0c3d4b9d81120fadccd5261be89ae8fd19))


### Bug Fixes

* **geminicli:** resolve extension names from local manifests to apply settings ([#338](https://github.com/GoogleCloudPlatform/evalbench/issues/338)) ([4d82afe](https://github.com/GoogleCloudPlatform/evalbench/commit/4d82afeb814c0221b75de94e208c15041f3d2b75))

## [1.4.0](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.3.1...v1.4.0) (2026-04-15)


### Features

* **scorer/llmrater:** add fallback to SQL logic comparison for empty results ([#326](https://github.com/GoogleCloudPlatform/evalbench/issues/326)) ([d168ac0](https://github.com/GoogleCloudPlatform/evalbench/commit/d168ac0233eb2fad3fb26d01086ac262a916d9cd))

## [1.3.1](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.3.0...v1.3.1) (2026-04-10)


### Bug Fixes

* **databases/alloydb:** restore correct use_adc flag behavior ([#315](https://github.com/GoogleCloudPlatform/evalbench/issues/315)) ([909e11d](https://github.com/GoogleCloudPlatform/evalbench/commit/909e11d29a095425b9f8247b4abcc9bc2fcb24d3))
* **generators/query_data_api:** add retry support for transient API errors ([#317](https://github.com/GoogleCloudPlatform/evalbench/issues/317)) ([e5fdead](https://github.com/GoogleCloudPlatform/evalbench/commit/e5fdeadcbab141b0e3cbb69bff72c874cb289085))

## [1.3.0](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.2.0...v1.3.0) (2026-04-09)


### Features

* Add summary_in_response and improve LLM rater resilience ([#311](https://github.com/GoogleCloudPlatform/evalbench/issues/311)) ([68b72ee](https://github.com/GoogleCloudPlatform/evalbench/commit/68b72ee375ac949e8601256125728b6dafc96622))

## [1.2.0](https://github.com/GoogleCloudPlatform/evalbench/compare/v1.1.0...v1.2.0) (2026-04-07)


### Features

* **adc:** support ADC for database authentication ([#306](https://github.com/GoogleCloudPlatform/evalbench/issues/306)) ([6cb05e6](https://github.com/GoogleCloudPlatform/evalbench/commit/6cb05e64e7993876971b465f7a8859ea5788e3ef))
* add Cloud Run support with entrypoint script, custom CSS, and environment-based XSRF configuration ([82fdeca](https://github.com/GoogleCloudPlatform/evalbench/commit/82fdeca112220560e83c4f7ccde16b4598ef0e5c))
* add UV_NO_SYNC support to run script and update Dockerfile and cloudbuild configuration accordingly ([43731f9](https://github.com/GoogleCloudPlatform/evalbench/commit/43731f90d138d920edf1e4ef6bf1000c0644ef3d))
* allow database name mapping via config ([#303](https://github.com/GoogleCloudPlatform/evalbench/issues/303)) ([3e8d25a](https://github.com/GoogleCloudPlatform/evalbench/commit/3e8d25aced3403611e26465533faccfb2449ad4d))
* **geminicli:** populate adc in fake home ([01c9c5b](https://github.com/GoogleCloudPlatform/evalbench/commit/01c9c5b7f1cc14861415f5aee8c3bb99da6ab2a0))
* **geminicli:** populate adc in fake home ([ce06c9b](https://github.com/GoogleCloudPlatform/evalbench/commit/ce06c9b934ace4c3d7a45bb502a26961c36583df))
* implement on_load logic to auto-select job directory from query parameters ([4691de4](https://github.com/GoogleCloudPlatform/evalbench/commit/4691de485c139c5a770e61161d3db7efa0b0e738))


### Bug Fixes

* consolidate experiment_config flag into util/flags.py ([#304](https://github.com/GoogleCloudPlatform/evalbench/issues/304)) ([432d11e](https://github.com/GoogleCloudPlatform/evalbench/commit/432d11e4813087f66a1b098bc4dbe8a57c4fb299))
* handle empty queries safely, ensure golden execution, and parse config robustly ([#265](https://github.com/GoogleCloudPlatform/evalbench/issues/265)) ([9ba022b](https://github.com/GoogleCloudPlatform/evalbench/commit/9ba022be63d43fb66ff04771efa82fa8feb0c04d))
* remove backticks from sanitized SQL strings ([#297](https://github.com/GoogleCloudPlatform/evalbench/issues/297)) ([4e4e201](https://github.com/GoogleCloudPlatform/evalbench/commit/4e4e2011fda461ef626648f4c0d67183064e1e9d))

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
