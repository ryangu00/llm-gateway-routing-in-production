# Day-one consumer acceptance

The historical incident is an authentication-path failure on a database-less LiteLLM 1.99.0 proxy. It is not a clean-room reproduction. The original deployment and discovery dates, 2026-09-02 and 2026-09-06, are reported only [R]. The whole-file traceback counts were measured on 2026-10-04 [M]; see [results](results.md#authentication-incident).

## Follow the credential to the caller

The consumer provider block referenced `${LITELLM_MASTER_KEY}`. The variable existed in a file read by the gateway launcher but not in the profile environment read by the multiplexed consumer. The consumer therefore sent no key. The important distinction is the environment used by the actual calling profile, not the environment visible from an operator's shell.

The recorded exception chain was:

```text
No api key passed in.
user_api_key_auth
_handle_authentication_error
is_database_service_unavailable_error
is_database_infrastructure_error
import prisma
ModuleNotFoundError: No module named 'prisma'
HTTP 500 Internal Server Error
```

The first failure entered database-related handling (`No connected db.`). Classifying that failure imported an absent optional package and raised a second exception. The client classified the resulting generic 500 as provider failure and used its own fallback. A process check could not detect a credential missing in another process.

## What the operator actually did

1. Read the consumer worker log that exposed fallback.
2. Inspect the proxy error chain and make an authenticated request. Six entry aliases returned 200 [R].
3. Back up the relevant profile files, put the key where each process reads it, and retain mode 600. Three profiles were fixed [R].
4. Run a real-call regression and require the consumer fallback-line count to be zero.
5. Inspect auxiliary configurations too: four title-generation configurations with empty API keys were redirected to a keyless local endpoint [R].

The separate 2026-10-03 observation was consistent with the missing-key failure. Authenticated model listing and a model call succeeded. No separate wrong-key request record was retained for 1.99.0.

## The acceptance adapter

[acceptance.sh](../day-one/acceptance.sh) requires an alias, that consumer's credential, and an artifact containing the consumer's fallback count for the accepted call. It asserts:

- HTTP 200 and a completion body.
- `x-litellm-model-group` equals the requested alias.
- `x-litellm-attempted-fallbacks` is absent or zero.
- The consumer's own fallback count is zero.

```bash
export ENTRY_ALIAS=entry-a
export GATEWAY_URL=http://127.0.0.1:4000
export CONSUMER_FALLBACK_FILE=consumer-fallback-count.txt
# Load LITELLM_MASTER_KEY from this consumer's own credential source.
bash day-one/acceptance.sh
```

Do not create a zero counter merely to satisfy this script. Obtaining and correlating that counter with a real consumer call is an integration prerequisite; no generic consumer API was supplied. The shell adapter validates the supplied counter and makes a credential-path request, but it cannot independently observe an arbitrary client's internal fallback chain. Port/model-list acceptance alone remains insufficient.

The catalog makes a narrower distinction: a probe with no credential reports `KEY_MISSING` instead of issuing a keyless request and calling the service down. In drift mode, a capacity-check family with no completed checks still fails. Missing credentials do not establish health.

## Reader reproduction candidate: not re-run by us

[auth_401_as_500.py](../probes/auth_401_as_500.py) is deliberately small and self-contained. It uses a fake loopback backend and a temporary LiteLLM process, with no configured database. It does not simulate the error handler or manufacture a 500. A valid-key control proves the fake completion route can be reached; missing and wrong-key cases print only HTTP status.

Provision LiteLLM 1.99.0 in an isolated environment first, with the optional `prisma` package absent. The exact clean dependency resolution was not established in the source. The script removes inherited database connection variables from the child environment, binds loopback, retains local diagnostics, and terminates its child process group.

```bash
python probes/auth_401_as_500.py --litellm .venv/bin/litellm
```

**This clean-environment reproduction was not run by us.** Its configuration has an offline test; that is not a reproduction of the authentication defect. It is also not a version-portability test.

## Upstream record and unresolved points

The dated source record [U, read 2026-10-04] describes a public LiteLLM issue opened 2026-08-31 against 1.98.0, titled "DB-less proxy returns generic 500 (not 401) for requests with no API key". It identifies five unguarded optional imports in `exception_handler.py`. It reports that installing `prisma` 0.15.0 restores 401, and that a wrong key returned 400 `No connected db.` on that version.

A fix PR guarding optional imports and another PR were still open in that record; the fix PR's base branch changed on 2026-09-23. No issue/PR identifiers or direct URLs were supplied for this publication, so no exact link is invented. Nothing was refreshed from the network.

We did not try that package workaround or retest a newer LiteLLM. The local note claiming wrong-key 500 on 1.99.0 has no separate request record. Preserve that discrepancy rather than merging missing-key and wrong-key behavior.

The configured `allow_requests_on_db_unavailable` flag is not an established remedy: a keyless request still returned 500 after it was enabled. Its effect on this path was not isolated.

## The callback negative control

The same 2026-09-06 audit found the spend ledger dead: one successful request added zero rows; the file held 32 rows, all `kind: fail`, from `2026-09-01T23:26:31` to `2026-09-02T15:52:27` [M]. Re-enabling that callback requires first proving that one successful request adds one row.

The audit also found only four consumer surfaces using the shared configuration rather than six planned, and recorded that the custom pre-call hook did not fire in 1.99. The cited upstream reports were not rechecked. The delivered purpose was a single cloud egress where content could be checked. The number of requests diverted during the authentication incident remains unknown.
