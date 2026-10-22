# awsl-wechat-chatlog native binaries

Source repo: https://github.com/awsldev/awsl-wechat-chatlog

Release: [v0.1.22](https://github.com/awsldev/awsl-wechat-chatlog/releases/tag/v0.1.22), built from `c4cb3231c7446e39d3ed12617a160939cb962e5c` by the [release pipeline](https://github.com/awsldev/awsl-wechat-chatlog/actions/runs/39771638916). All three build jobs and the publish job succeeded. Repository binary update: `1ea2268`.

All runtime binaries below are the unmodified release assets. Their SHA256 digests were checked against the GitHub release metadata and the upstream repository files. The Linux amd64 compatibility alias is byte-for-byte identical to the architecture-specific asset.

Runtime assets:
- `awsl_wechat_chatlog.dll` (Windows amd64)
- `libawsl_wechat_chatlog.so` (Linux amd64 compatibility alias)
- `libawsl_wechat_chatlog_linux_amd64.so`
- `libawsl_wechat_chatlog_linux_arm64.so`

Headers are not required at runtime and are intentionally not vendored.

SHA256:
- `awsl_wechat_chatlog.dll`: `0b94302ffb2104d7b890c2056bae6c0e8342d01741da01e889b2fb8a23c3b068`
- `libawsl_wechat_chatlog.so`: `6e0521c6fcf0d977f769d580b82543797fe3102918b72bec0fdce1c88a306422`
- `libawsl_wechat_chatlog_linux_amd64.so`: `6e0521c6fcf0d977f769d580b82543797fe3102918b72bec0fdce1c88a306422`
- `libawsl_wechat_chatlog_linux_arm64.so`: `93ba5723fcdcfb3bf1b484c4a622f8812520cb1fd685c0a3109ff1fd859875b7`

## Linux read-only access

The Linux read-only fix is included upstream in v0.1.22; no local patch or rebuild is needed. Linux uses an absolute `file:` URI with `mode=ro` and without `nolock`, keeps WAL reading enabled, and does not set `immutable`. Normal SQLite SHM read-lock coordination is allowed.

The release pipeline runs `go test ./...` and Python wrapper tests before building each platform. The upstream regression in `cmd/native/readonly_linux_test.go` holds an encrypted WAL writer open, verifies new WAL rows remain visible, rejects writes, and checks WAL bytes remain unchanged by the reader. Linux amd64 release-asset loading, live WAL visibility, rejected writes and unchanged WAL bytes were also rechecked on the deployment host. Windows/ARM64 were built and tested by CI; their binaries were not exercised on that host.

This update does not add database repair or automatic key recapture. Keep the per-database key JSON current when WeChat creates/rebuilds databases.
