---
profile_name: my-prod-vpn
ovpn_provision_cmd: make get_vpn_client_config OUTPUT=~/.config/openvpn3/my-prod-vpn.ovpn
trigger_patterns:
  - "mysql .* -h [^ ]*\\.internal"
  - "kubectl --context prod-.*"
  - "aws (rds|elasticache|memorydb|secretsmanager|ssm) "
---

# Project VPN notes

Copy this file to `.claude/openvpn3-on-demand.local.md` at the project root and
edit the frontmatter. See the plugin README for field documentation.
