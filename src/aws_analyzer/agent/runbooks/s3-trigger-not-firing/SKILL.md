---
name: s3-trigger-not-firing
description: Files uploaded to S3 should have started a Lambda function and didn't, or only some of them did. Use when the user says a file arrived but nothing ran, or asks whether last night's uploads were processed.
metadata:
  toolkits: s3 lambda
---
# An S3 upload didn't start its Lambda

An upload starts a function only when every link of the chain holds:
the file matches a notification → the bucket may invoke the function → the function ran → the run succeeded.
Find the first link that broke.

1. **The links.** Call `s3_triggers` with the bucket, the function, or both, over the user's window. If you have
   neither, ask which bucket, or use `find_buckets`, then `bucket_details`, to find the bucket whose notifications
   name a function.
2. **No notification covers the file.** Compare the key the user means with each link's prefix and suffix. They are
   case-sensitive (`.CSV` isn't `.csv`), and a prefix needs its trailing `/`. A multipart upload sends
   `s3:ObjectCreated:CompleteMultipartUpload`, not `Put`. If no link covers the files, stop. Name the rule that
   missed and the change to make to it.
3. **The function can't be invoked.** If `s3_triggers` says the function is missing, or its resource policy doesn't
   let this bucket invoke it, stop. Give the `aws lambda add-permission ...` command, with the bucket's ARN and the
   account as source.
4. **Files matched, but nothing ran.** Compare the files the link covers in the window with the function's calls. No
   calls means S3 didn't deliver: check the notification's event types and whether the function is in the bucket's
   region. Fewer calls than files means some were throttled (see `function_health`).
5. **It ran and failed.** Follow the lambda-failing runbook from step 3. Call `function_errors`, then
   `function_logs` with `search=` a file's key to find the run of one file.

In the answer, say which link broke, for which files (how many, and an example key), and the one change that fixes
it. Nothing here changes a bucket or a function: give the setting or the `aws ...` command for the user to run.
