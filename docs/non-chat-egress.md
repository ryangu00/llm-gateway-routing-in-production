# Non-chat routes through one egress

The source case involved two aggregator endpoints outside the chat-model list. The product and vendor are intentionally generalized. The public names below are placeholders; they are not a verified provider contract.

## Gateway-side credential injection

```yaml
general_settings:
  pass_through_request_timeout: 60
  pass_through_endpoints:
    - path: /decide/decisions
      target: https://aggregator.example/api/alpha/decisions
      methods: [POST]
      headers:
        Authorization: Bearer os.environ/AGG_API_KEY
        content-type: application/json
        accept: application/json
    - path: /decide/sdk
      target: https://aggregator.example/api/v1/sdk-route
      methods: [POST]
      headers:
        Authorization: Bearer os.environ/AGG_API_KEY
        content-type: application/json
        accept: application/json
```

The caller authenticates to the gateway; the aggregator credential is injected by the gateway process. A non-chat endpoint needs this explicit mapping even when chat already uses the same aggregator. Content checking must apply to gateway-bound traffic despite the gateway's loopback address.

## Historical procedure

The operator backed up the config, inserted the route block after the `allow_requests_on_db_unavailable` anchor, parsed the result with the gateway's Python/PyYAML, restarted, polled liveliness, compared the authenticated model count, called a public sample, and printed rollback. Existing `pass_through_endpoints` made the recipe idempotent.

The observed model-count record was 27 unchanged [R]. The direct and gateway timings are single calls under different paths, including a tunnel for the gateway request, so their difference is not overhead. All timings and cost claims remain reported only; the aggregate input-token count has a measured tag. [The complete table](results.md#non-chat-egress) retains those distinctions.

The original configuration script relied on a textual anchor. The public adaptation still asserts that exact anchor, then uses PyYAML to produce a candidate and reads it back. It checks model-list count during transformation and authenticated `/v1/models` count across restart in apply mode.

## Offline candidate edit

```bash
cp examples/gateway.example.yaml candidate.yaml
bash recipes/add_passthrough.sh --config candidate.yaml
```

The default backs up and modifies only the supplied candidate. It prints the route list, configured model count, and an exact copy-based rollback command. It makes no HTTP requests and does not restart a service. Tests call the same transformation and verify idempotence, missing-anchor refusal, and preserved model count.

For an explicitly prepared deployment, `--apply --service-label <launchd-target>` enables the restart and probes. Supply a correct gateway URL, model identifier, credential environment, and working provider target first. The source does not supply a public decision-model identity or current endpoint contract, so the shipped `aggregator.example` values cannot serve live requests unchanged.

Apply mode records the pre-restart model count, writes the validated file, restarts, polls liveliness for up to 40 iterations with a one-second interval, checks the post-restart count, and calls the sample route. It prints only id, model, usage, HTTP status, and time. The rollback line is printed before restart so it remains available after a failed probe.

The offline tests inject a fake requester and restart function. They cover the apply branch without invoking launchd or contacting a provider. This does not establish that a real service restarted or the real pass-through endpoint works.

## Relationship to the route gate

The gate's four change kinds do not authorize arbitrary top-level pass-through additions. The historical recipe is not a fifth gate kind. Use it on a candidate or a disposable gateway to review the change; do not assume it grants permission to bypass a root-owned production config.

A protected production deployment would need an explicit reviewed change path for this top-level configuration. Such an extension is outside the supplied implementation. The public recipe does not alter the gate to accept it and does not invoke the privileged applier automatically.

## Failure modes and limits

The local listening port can belong to a tunnel rather than the service. The recorded procedure used listener inspection and config-hash comparison to locate the actual file. Running a correct script against the wrong copy changes nothing at the served endpoint.

An address exemption in an outbound content guard can turn a local gateway into a cloud bypass. The recorded fix was to carve the gateway port out of the exemption and drill both directions: backend-bound local traffic allowed, gateway-bound content judged. The guard implementation is not shipped here.

A private client wrapper was described as environment-keyed, gateway-default, and rejecting sensitive-looking state. Its implementation and public contract were not supplied, so it is not recreated from guesses. A direct debugging mode must be accounted for as another egress, even when the shell-level guard cannot see runtime clients.

Nothing in the single-call results establishes pass-through performance under load. The reusable result is the route/injection/validation procedure and its explicit limitations.
