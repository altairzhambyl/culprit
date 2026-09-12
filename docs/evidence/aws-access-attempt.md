# Attempt to reach AWS from the build environment — 2026-09-12T15:49:08Z

## Credentials present in the environment

AWS_SECRET_ACCESS_KEY=prox… (redacted; proxy placeholder, not a real key)
AWS_ACCESS_KEY_ID=prox… (redacted; proxy placeholder, not a real key)

## boto3 sts get-caller-identity

us-east-1 FAILED: Failed to connect to proxy URL: "http://<local-proxy>"
us-west-2 FAILED: Failed to connect to proxy URL: "http://<local-proxy>"

## bedrock-runtime converse (Strands default model id)

FAILED: Failed to connect to proxy URL: "http://<local-proxy>"

## Egress proxy verdicts for AWS hosts (from the sandbox proxy status)

- bedrock-runtime.us-east-1.amazonaws.com:443: connect_rejected — gateway answered 403 to CONNECT (policy denial or upstream failure)
- bedrock-runtime.us-west-2.amazonaws.com:443: connect_rejected — gateway answered 403 to CONNECT (policy denial or upstream failure)
- bedrock.us-east-1.amazonaws.com:443: connect_rejected — gateway answered 403 to CONNECT (policy denial or upstream failure)
- bedrock-agentcore-control.us-east-1.amazonaws.com:443: connect_rejected — gateway answered 403 to CONNECT (policy denial or upstream failure)
- sts.amazonaws.com:443: connect_rejected — gateway answered 403 to CONNECT (policy denial or upstream failure)
- sts.us-east-1.amazonaws.com:443: connect_rejected — gateway answered 403 to CONNECT (policy denial or upstream failure)
- sts.us-west-2.amazonaws.com:443: connect_rejected — gateway answered 403 to CONNECT (policy denial or upstream failure)

## Conclusion

All AWS endpoints (STS, Bedrock, Bedrock Runtime, AgentCore control plane) are denied by the build environment's egress policy, and the only AWS credentials present are proxy placeholders. No real Bedrock investigation and no AgentCore deployment could be executed from this environment. Nothing in this repository claims otherwise.
