---
name: lambda-failing
description: A Lambda function is failing, erroring, timing out or being throttled, or the user asks why one broke or which one failed. Use to find the failing function, the cause and the line of code behind it.
metadata:
  toolkits: lambda
---
# A Lambda function is failing

Work from cheap and broad to narrow. Stop as soon as you can name the cause and the fix.

1. **Which function, and since when.** If the user didn't name a function, call `function_health` over their window
   (default: the last 24 hours) and take the functions in `failed`, `throttled` and `near_timeout`. If one did, still
   call it with `match=` set to that name, to get its calls, failures and throttles over the window.
2. **Throttled, not failing?** Throttles mean calls were refused before they ran. Call `function_details` and look at
   reserved concurrency and the account's concurrency. The fix is a setting, not code. Say which one, and stop here.
3. **What failed.** Call `function_errors` for the function. Take the cause with the most calls first, and note when
   it first happened and which versions it ran on (`ran_on`).
4. **Timeouts.** If the cause is `Timeout`, call `function_performance`. Compare the longest runs with the timeout,
   and cold starts with memory. Find what the slow runs wait on: `search_code` for `timeout` or for the client they
   call.
5. **Into the code.** Each cause's `in_code` names a file and a line. Read it with `function_source(function,
   file=..., lines=...)`. If the error names a function of the code, use `find_symbol` to see where it's defined and
   who calls it. Quote the line that raised.
6. **What changed.** If the errors started recently, call `diff_versions` and say what changed in the code, the
   libraries or the settings around the time they started.
7. **One run, in detail.** If you still can't tell, call `function_logs` with `search=` one of the cause's request
   IDs to read that run's lines.

In the answer, give: the function, how many calls failed in the window, the cause and its line, what to change, and
when it started. A fix that changes the function (memory, timeout, code) is for the user to make. Give the setting or
the `aws lambda ...` command.
