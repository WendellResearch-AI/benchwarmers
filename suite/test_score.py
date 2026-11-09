"""python suite/test_score.py — the scorer's negation guard and first-line scoring, and the
uptime classifier's cases; no calls."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import run  # noqa: E402

trap = {"must_find": [{"any_of": ["NO DATABASE WRITES"]}], "must_not_claim": ["INSERT"],
        "min_answer_chars": 5}
# Ruling something out is not claiming it.
assert run.score(trap, "NO DATABASE WRITES: there are no INSERT or DELETE statements.")["invented"] == []
assert run.score(trap, "NO DATABASE WRITES. It does not INSERT anything.")["invented"] == []
# Asserting it still counts, including after an earlier negated sentence.
assert run.score(trap, "Line 12 runs an INSERT into settings.")["invented"] == ["INSERT"]
assert run.score(trap, "No reads here. Line 12 runs an INSERT.")["invented"] == ["INSERT"]

claim = {"must_find": [{"any_of": ["verdict: false"]}], "must_not_claim": ["verdict: true"],
         "score_in": "first_line", "min_answer_chars": 5}
assert run.score(claim, "VERDICT: FALSE\nquoted line ...")["found"] == 1
# Reasoning that mentions the other verdict later is not scored.
r = run.score(claim, "VERDICT: FALSE\nOne might think VERDICT: TRUE, but ...")
assert r["found"] == 1 and r["invented"] == []
# A verdict not on the first line does not count.
assert run.score(claim, "Let me think.\nVERDICT: FALSE")["found"] == 0
seeded = {"must_find": [{"regex": r"^fault:\s*line\s*1\b", "label": "fault: line 1"}],
          "must_not_claim": ["no fault"], "score_in": "first_line", "min_answer_chars": 5}
assert run.score(seeded, "FAULT: line 1\nthe comparison ...")["found"] == 1
assert run.score(seeded, "FAULT: line 12\n...")["found"] == 0      # not a prefix match
assert run.score(seeded, "NO FAULT")["invented"] == ["no fault"]

# Every class failure_class returns has a place in the worst-first order (perf/run.py
# wrote a blank failure for signin while it had none).
assert run.failure_class("Error: Error code: 401 - {'error': 'invalid key'}") in run.FAILURE_CLASSES

# run.uptime_class: shapes from the bench's own data first, then public providers'
# documented formats and the heavy-hitters panel's cases (2026-11-08).
UPTIME = {
    "Error: Error code: 429 - {'error': {'message': 'Provider returned error', 'code': 429, 'metadata': {'raw': 'x/y:free is temporarily rate-limited upstream. Please retry shortly, or add your own key to accumulate your rate limits'}}}": "busy",
    "Error: Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day', 'code': 429}}": "quota",
    "s. Credits don't affect this cap. Add your own keys to get a boost.', 'code': 429, 'metadata': {'limit_source': 'openrouter_shared_capacity'}}": "busy",
    "Error: Error code: 429 - {'error': {'message': 'Too many requests. The free tier allows 1 request(s) every 1 min per account on m:free - nothing is used up, retry in 60s.'}}": "rate",
    "Error: Error code: 503 - {'error': {'message': 'The free usage quota for this model is used up and it is currently unavailable.'}}": "quota",
    "Error: Error code: 503 - {'error': {'code': 'get_channel_failed', 'message': 'All providers for model m are busy right now (they hit their rate limit).'}}": "busy",
    "Error: Error code: 500 - ResourceExhausted: Worker local total request limit reached (33/32)": "busy",
    "Error: Error code: 404 - {'error': 'The requested model is currently unavailable.', 'error_type': 'unavailable_model'}": "setup",
    "Error: Error code: 400 - {'error': {'code': 400, 'message': 'Request contains an invalid argument.', 'status': 'INVALID_ARGUMENT'}}": "setup",
    "Error: Error code: 413 - Request too large for model in organization on input tokens per minute (ITPM): Limit 7000, Requested 9000": "setup",
    "Error: Error code: 429 - Your organization reached its limit of concurrent requests. top up at <url>": "rate",
    "Error: Error code: 402 - {'error': {'title': 'Paid Model - Credits Required', 'message': 'Add credits to continue'}}": "setup",
    "Error: Error code: 429 - {'error': {'message': \"You've reached today's free-model token quota. Your plan's paid allowance is separate, or wait for the daily reset.\"}} Retry-After: 14652": "quota",
    "Error: client failed: Connection error. ([WinError 10061] No connection could be made because the target machine actively refused it)": "relay",
    "Error: client failed: the reply broke off part way ([WinError 10054] An existing connection was forcibly closed by the remote host)": "unreachable",
    "Error: client failed: the reply had no choices: the stream sent none": "unreachable",
    "<<TIMED OUT after 90s>>": "timeout",
    "no answer in 420s": "timeout",
    "Error: client failed: rate limit (not sent; paced by the bench): x allows 50 requests per day": "notsent",
    "sqlite3.OperationalError: database is locked": "client",
    "Error: Error code: 429 - {'error': {'code': 'model_cooldown', 'last_upstream_error': 'usage_limit_reached: The usage limit has been reached', 'message': 'All credentials for model m are cooling down via provider codex'}} Retry-After: 2528616": "quota",
    "Error: Error code: 503 - {'error': {'message': 'The model is temporarily unavailable due to high demand. Please retry in a moment.', 'type': 'server_error'}}": "busy",
    "Error: Error code: 418 - {'error': 'teapot'}": "unrecognised",
    "Error: Error code: 529 - {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}}": "busy",
    "Error: Error code: 429 - {'error': {'code': 429, 'message': 'You exceeded your current quota', 'status': 'RESOURCE_EXHAUSTED'}}": "quota",
    "Error: Error code: 429 - {'error': {'code': 429, 'message': 'Resource has been exhausted (e.g. check quota).', 'status': 'RESOURCE_EXHAUSTED'}}": "busy",
    "Error: Error code: 429 - {'type': 'error', 'error': {'type': 'rate_limit_error', 'message': 'Number of request tokens has exceeded your per-minute rate limit'}}": "rate",
    "Error: Error code: 429 - {'error': {'message': 'Rate limit reached for gpt-x in organization org-x on requests per min (RPM): Limit 3, Used 3, Requested 1.', 'type': 'requests', 'code': 'rate_limit_exceeded'}}": "rate",
    "Error: Error code: 429 - {'message': \"You are using a Trial key, which is limited to 1000 API calls / month.\"}": "quota",
    "Error: Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-min.', 'code': 429}}": "rate",
    "Error: Error code: 400 - {'error': {'message': 'Provider returned error: prompt is too long: 210000 tokens > 200000 maximum', 'code': 400}}": "setup",
    "Error: Error code: 400 - {'message': 'Prompt exceeds the capacity of this model: too many tokens'}": "setup",
    "Error: Error code: 503 - {'error': 'Service temporarily unavailable during daily maintenance window'}": "busy",
    "Error: Error code: 503 - Your account is temporarily overloaded.": "busy",
    "Error: Error code: 500 - Your account failed to generate a reply.": "busy",
    "403: Your account does not have access to this model.": "setup",
    "An error occurred (ServiceQuotaExceededException) when calling the InvokeModel operation: Rate exceeded.": "quota",
    "An error occurred (AccessDeniedException) when calling the InvokeModel operation: You don't have access to the model.": "setup",
    "An error occurred (ValidationException) when calling the InvokeModel operation: The provided model identifier is invalid.": "setup",
    "An error occurred (ModelNotReadyException) when calling the Converse operation: Model is not ready for inference.": "busy",
    "An error occurred (ThrottlingException) when calling the InvokeModel operation: Too many requests, please wait.": "busy",
    "{\"error\": \"model 'llama3:70b' not found, try pulling it first\"}": "setup",
    "{\"success\": false, \"errors\": [{\"code\": 10000, \"message\": \"Authentication error\"}]}": "setup",
    "... 'code': 'content_filter', 'status': 400}}": "setup",
    "Error: Error code: 401 - {'error': {'message': 'Provider returned error', 'metadata': {'raw': 'invalid api key'}}}": "busy",
    "Error: Error code: 429 - {'error': {'message': 'Too many requests'}} Retry-After: 86400": "quota",
    "Error: Error code: 429 - {'error': {'message': 'Too many requests'}} Retry-After: 30": "busy",
    "Error: Error code: 503 - model unavailable for free users due to high demand": "busy",
    "provider notice in a success reply (out of credits): You are out of credits.": "quota",
    # the heavy-hitters review of the build (p20261108-104415-db82)
    "Error: Error code: 403 - {'error': {'message': 'Provider returned error', 'metadata': {'raw': 'Unauthorized'}}}": "busy",
    "Error: Error code: 403 - {'error': {'message': 'Unauthorized'}}": "setup",
    "Error: Error code: 429 - {'error': {'message': 'Too many tokens per minute'}}": "rate",
    "Error: Error code: 502 - Bad Gateway at https://example.com/api/day/status": "busy",
    "Error: client failed: the reply broke off part way (connection reset while fetching today's list)": "unreachable",
    "Error: Error code: 429 - Model is experiencing high demand. Please retry shortly, or add your own key for a higher daily limit.": "busy",
    "[WinError 10065] No connection could be made to the host": "relay",
    "ne 12, in handler ... Traceback (most recent call last): ... 'status': 500, 'error code: 500'": "busy",
}
# The rules alone (decided=False): what this install's decision models said of a shape is not a rule.
bad = [(t[:80], run.uptime_class(t, private_endpoint=True, decided=False)[0], want) for t, want in UPTIME.items()
       if run.uptime_class(t, private_endpoint=True, decided=False)[0] != want]
assert not bad, bad
# The call's own status decides first; an error with no text is never an answer.
assert run.uptime_class("", "ok")[0] == "ok" and run.uptime_class("", "empty")[0] == "empty"
assert run.uptime_class("", "refused")[0] == "notsent" and run.uptime_class("", "timeout")[0] == "timeout"
assert run.uptime_class("", "error")[0] == "unrecognised"
# A refused connection is our hub only when the call went to one.
assert run.uptime_class("Error: client failed: Connection error. (connection refused)")[0] == "unreachable"
assert set(run.UPTIME_UP + run.UPTIME_DOWN + run.UPTIME_OUT) >= {run.uptime_class(t, decided=False)[0] for t in UPTIME}
# A normal answer never reaches the notice fallback (metrics/test_decide.py tests the fallback itself).
assert not run.NOTICE_LIKE.search("ready") and run.NOTICE_LIKE.search("your credit balance is low")
print("ok")
