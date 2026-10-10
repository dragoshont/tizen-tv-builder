# Tizen TV Builder

A small, evidence-driven toolkit and coding agent for Samsung retail-TV Web applications.
Designed to investigate **2015 through current model groups**, not promise every TV works.
Useful for Plex, Immich and other media clients without coupling them to one backend.

**Status:** initial toolkit; offline tests cover validation/command contracts. The lessons include
observed development on one 2017 TV. This repository's commands have not yet completed a new
retail-TV acceptance run. Native screenshots/recording are not promised.

## Start here

You need Python 3.10+, the supported Tizen Studio/TV Extension for your host and target,
and a Samsung developer certificate profile. This repository does not install them.
SDK wrappers require a POSIX host (macOS/Linux). Timeout or cancellation (Ctrl-C/SIGTERM)
stops only their owned process group and reaps the launcher. On Windows, use Samsung's CLI directly;
manifest validation and HTTP inspector discovery do not launch SDK processes.

```sh
python3 tools/tizen.py doctor
python3 tools/tizen.py validate examples/hello-tv
python3 tools/tizen.py build examples/hello-tv
python3 -m unittest discover -s tests -v
```

`build` uses Samsung's CLI with temporary output. It is unsigned and does not contact the TV.
`package` stages only manifest, HTML, JS, CSS and common image/font assets; certificate files,
private configuration, generated packages and symlinks are rejected/excluded. Keep personal
data outside the source tree: file extensions do not make a source file safe.

```sh
python3 tools/tizen.py package examples/hello-tv --profile YOUR_PROFILE --output /private/path/hello.wgt
python3 tools/tizen.py install /private/path/hello.wgt --serial TV_ADDRESS:26101 --authorized
python3 tools/tizen.py run --app PlxHelloTv.Hello --serial TV_ADDRESS:26101 --authorized
```

Before first installation, use Samsung Certificate Manager to create a **Samsung TV** profile
(author and distributor certificates), add the target DUID, then permit installation using
Device Manager or `tizen install-permit -s TARGET`. `install` does not automatically grant
certificate access. It does not create a lock: the caller must hold exclusive device ownership.
`--authorized` is an explicit acknowledgment, not a mechanism that proves owner consent.

Set `TIZEN_SDK_ROOT` if the SDK is not at `~/tizen-studio`; alternatively use `--tizen PATH`
and `--sdb PATH`. No commands invoke a shell or request root.

## Web Inspector

```sh
python3 tools/tizen.py debug --app PlxHelloTv.Hello --serial TV_ADDRESS:26101 --authorized --legacy-timeout 0
# Read the debug port returned by the TV, then:
python3 tools/tizen.py forward --port DEBUG_PORT --serial TV_ADDRESS:26101 --authorized
python3 tools/tizen.py inspect --port DEBUG_PORT
# When finished, remove only your own forward:
python3 tools/tizen.py unforward --port DEBUG_PORT --serial TV_ADDRESS:26101 --authorized
```

The debug response is displayed because its returned port is required; it never lists installed
apps or credentials. `inspect` is loopback HTTP discovery with a deadline/body limit and prints
only browser/protocol, target count and page types, not titles/URLs/DOM or tokens.
Each discovery request has a five-second total deadline covering connection, request,
status line, headers and body (at most ten seconds for the two endpoints). Slow trickled
bytes do not reset it; connections/responses are closed on failure.

On the observed 2017 TV, extra debug argument `0` worked; screenshots timed out and
screencasting was explicitly unavailable. Do not confuse an inspector connection with image
capture. See [lessons](docs/lessons.md) and [compatibility](docs/compatibility.md).

## Agent

The portable agent prompt is [.agents/agents/tizen-tv-builder.md](.agents/agents/tizen-tv-builder.md).
Copy or register it using your coding harness's documented custom-agent mechanism, keeping its
repository docs available. There is no universal agent installer claimed here. The matching
[skill](.agents/skills/tizen-tv/SKILL.md) describes the development loop.

For a shareable demo, record an isolated desktop browser using original synthetic content and
label it as a desktop demo. Native TV capture, DOM reconstruction and phone recordings are
different artifacts. No Reddit upload or public support announcement happens automatically.

MIT-licensed, independently written tools. No Samsung SDK or PlxNative GPL implementation is
bundled. The source-backed findings acknowledge [PlxNative](https://github.com/dragoshont/plx-native).
