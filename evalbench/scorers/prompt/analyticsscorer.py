"""Prompts and Rubrics for the Analytics Scorer (Brewmax Data Result Rater)."""

DATA_RESULTS_RUBRIC = """This rubric is satisfied if the trial data result correctly addresses the user prompt and key outcomes in the ground truth data result are present in the trial data result even if the ground truth and trial data results don\'t match exactly. Accordingly, if the ground truth trajectory contains a data result with non-null data (a.k.a. the trajectory includes a non-null data result, after the \'Data:\' prefix), but the trial trajectory doesn\'t contain a data result (a.k.a.  the trajectory doesn\'t include a \'Data:\' prefix followed by a non-null data result) then the rubric is not satisfied. Similarly, if the ground truth trajectory doesn\'t contain a data result, but the trial trajectory does contain a data result, then the rubric is not satisfied. However, below are some specific cases where the trial data result should be considered correct even if it isn\'t identical to the ground truth data result. Thus, do not penalize the trial data result when it differs from the ground truth data result because:
    1) It contains the same content as the ground truth data result, but the column names, column order, row order, etc. differ and no ordering or column name requirements are specified in the user prompt.
    2) The user prompt asks for a count, but doesn’t specify whether this count should be all or only unique instances and it\'s not obvious / implicit whether uniqueness is important. This ambiguity leads one of the trial/ground truth trajectories to count \'distinct\' cases and the other to count all cases, generating different data results. In this case, both the trial and ground truth data results correctly answer the user prompt as long as all other logic to get each result is valid.
    3) The trial data result performs rounding differently from the ground truth data result and the user prompt does not provide specific guidelines for integer/decimal rounding.
    4) The user prompt requests the \'first\' or \'top\' X entries of a list, but doesn’t specify the field to use for ordering, causing trial and ground truth data results to contain different subsets of the list derived from different orderings.
    5) The user prompt doesn’t specify whether to include or exclude \'null\' OR \'NA\' values in the result, causing trial and ground truth data results to differ on whether they include vs. exclude \'nulls\' or \'NAs\'.
    6) The user prompt asks for the \'top\'/\'highest\' or \'bottom\'/\'lowest\' entries that meet a particular criteria, but doesn’t specify a concrete number of entries, causing trial and ground truth data results to feature different numbers of entries.
    7) The user prompt asks for a list of items, but doesn\'t specify whether to return the items\' IDs or names, leading trial and ground truth data results to differ in whether they return IDs or names of the correct items.
    8) The trial data result contains a small number of extra columns relative to the ground truth data result that were not explicitly requested to be included or excluded in the user prompt and do not cause the overall trial data result to be incorrect.
    9) The trial data result is truncated differently from the ground truth data result. For example, if the trial data result addresses the user prompt, do not penalize if the trial data result and ground truth data result are truncated from different numbers of rows for display (e.g., the trial data result is truncated from 3000 rows to 50 rows for display whereas the ground truth dataframe is truncated from 1000 rows to 50 rows for display). Additionally, if the trial data result addresses the user prompt, do not penalize if the trial and ground truth data results have different numbers of rows after truncation (e.g., the trial data result is truncated to 25 rows for display whereas the ground truth data result is truncated to 50 rows for display). Additionally, if ordering is not specified in the user prompt, do not penalize if the trial data result addresses the user prompt, but due to truncation, contains a different subset of data from the ground truth data result.
   10) The user prompt requests the \'first\' or \'top\' X entries of a list, but fewer than X entries are featured in the data result because fewer than X entries meet the criteria.
   11) The user prompt requests a subset of data relative to the current time and/or date (e.g., all entries from the last two years that meet X criteria), and all logic to obtain the trial data result is valid, but the trial vs. ground truth data results differ because the ground truth query was run at a different time from the trial query.

The rubric is not satisfied if according to the above criteria, the trial data result does not correctly address the user prompt."""

ANALYTICS_SCORER_PROMPT_TEMPLATE = """Your task is to check how Conversational Analytics agent trial responses to a user prompt compare to ground truth Conversational Analytics responses for a single conversational turn.
Note that the ground truth responses serve as a reference for the trial responses, not a strict template that must be matched exactly.
Below is the rubric which determines how to evaluate trial responses given ground truth responses.

For each turn, based on the above rubric, the rater will return a rating given three types of information: 1) a user prompt that responses attempt to address, 2) the ground truth responses to address the user prompt, and 3) the trial responses to address the user prompt.
When returning a rating that is less than 1.0, the rater must justify this rating by clearly documenting the shortcomings of the trial response in the provided explanation.

Output ratings corresponding to the rubric for each property, like as follows:

# Rubric
{rubric}

# User Prompt
{user_prompt}

# Ground Truth Trajectory
{ground_truth_trajectory}

# Trial Trajectory
{trial_trajectory}

End your evaluation with exactly one of the following labels: `VERDICT: PASS` or `VERDICT: FAIL`."""
