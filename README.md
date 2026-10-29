# Samsung Pointer for Home Assistant

Local mouse-pointer control for compatible Samsung Tizen TVs. Designed to run
alongside **SamsungTV Smart** without modifying its files or importing its internals.
Both integrations and Universal Remote Card can be updated independently.

**Early release.** The `ProcessMouseDevice / Move` command was visually confirmed
on a Samsung S90D (QE65S90DATXXH). This is not a claim that every Tizen TV or app
supports a pointer. Click commands and the complete dashboard experience still
need verification on real TVs. The TV does not acknowledge mouse commands.

## Install with HACS

1. Open HACS → menu → **Custom repositories**.
2. Add `https://github.com/IgorsPopovs/ha-samsung-pointer`, type **Integration**.
3. Download **Samsung Pointer** and restart Home Assistant.
4. Open Settings → Devices & services → Add integration → **Samsung Pointer**.
5. Enter your TV's local IP/hostname and a display name. Keep the TV switched on.
6. Start pairing and approve **Home Assistant Samsung Pointer …** on the TV.
   Setup waits up to 60 seconds and sends no mouse or key commands.

Requires Home Assistant **2026.10.0 or newer**. Reserve the TV's IP address in your
router if possible. The initial release uses TLS port 8002 and requires a reusable
pairing token. There is no SmartThings account or cloud dependency.

HACS will offer updates from this repository's releases. It does not automatically
merge changes from other projects. SamsungTV Smart remains installed and updated
from its original repository.

## Actions

The integration creates a `remote` entity named **Pointer** under the chosen TV
name. Use its actual entity ID in the examples; `remote.living_room_tv_pointer`
below is only a placeholder.

| Action | Data | Meaning |
| --- | --- | --- |
| `samsung_pointer.move` | `x`, `y` | Relative mouse movement; positive right/down, negative left/up. |
| `samsung_pointer.click` | `button: left` or `right` | One complete mouse click; model/app dependent, experimental. |
| `remote.turn_on` | Target the Pointer entity | Open the pointer connection; does **not** switch on the TV. |
| `remote.turn_off` | Target the Pointer entity | Close the pointer connection; does **not** switch off the TV. |

The Pointer entity's on/off state reports its socket connection, **not TV power**.
It loads while the TV is off and connects on the first action. That first gesture
is discarded after reconnect to prevent delayed jumps. For immediate response,
connect with `remote.turn_on` before using the pad. Clicks are never replayed after
reconnect: click again once the connection is ready.

```yaml
action: samsung_pointer.move
target:
  entity_id: remote.living_room_tv_pointer
data:
  x: 12
  y: 0
```

No scroll, held-button, text-entry, power or app-launch commands are implemented.
Continue using your current TV integration for those features.

## Precision trackpad (v0.2.0)

The integration now bundles its own card, **Samsung Pointer**. It captures every
pointer sample, uses a radial drag threshold (including diagonals), preserves
fractional movement, and sends at most one Home Assistant request at a time.
It explicitly disables the backend's sample-size acceleration for its movements.
Existing Universal Remote Card configurations continue using their own settings.

1. Update the integration and restart Home Assistant.
2. Add a dashboard resource of type **JavaScript module**:
   `/samsung_pointer/samsung-pointer-card.js?v=0.2.0`.
3. Add the card using your actual Pointer entity:

```yaml
type: custom:samsung-pointer-card
entity: remote.living_room_tv_pointer
sensitivity: 1.5
interval: 25
```

Use **Подключить** to connect, drag to move, tap briefly or press **Клик** to click.
Lifting the finger lets you reposition it without moving the cursor. The sensitivity
slider changes gain for the current card session; use `sensitivity` in the card
configuration to save a default. Controls currently use Russian labels.
The focusable pad also supports arrow keys and Enter/Space. No inertia, smoothing,
or acceleration is applied. A long stationary hold does not click.

`interval` accepts 16–50 ms (default 25); `sensitivity` accepts 0.25–4.
Movement samples older than 100 ms are dropped instead of replayed after a stall;
normal short batches retain their total displacement. Cancellation, a second
finger, page hiding, or card removal clears unsent movement. A short final batch
may still be delivered after finger lift; already sent commands cannot be recalled.
A slow network can still delay the current in-flight command, so this is not a
hard end-to-end latency guarantee. Clicks are refused while movement is pending.

`show_latency: true` shows the last HA service-call response time, **not** the time
until the cursor appears on the television. Physical TV response must be assessed
on the TV. The bundled static file contains no device addresses or credentials;
all commands use the current authenticated HA connection and the configured entity.
The file is served without long-lived cache headers; use a version query when
updating the dashboard resource and reload open dashboards after an update.

The card and integration update together through HACS. No second repository is
required. For rollback, restore your previous remote card configuration and remove
the added resource. The existing integration entry and pairing can remain.

## Universal Remote Card

The existing card supports `drag_action`; a card fork is not needed. Add this
custom element and put `pointer` in `rows`. Keep a normal `touchpad` or `circlepad`
available for apps that only support arrow navigation.

```yaml
custom_actions:
  - type: touchpad
    name: pointer
    icon: mdi:cursor-default
    drag_action:
      action: perform-action
      perform_action: samsung_pointer.move
      target:
        entity_id: remote.living_room_tv_pointer
      data:
        x: '{{ deltaX * 2 }}'
        y: '{{ deltaY * 2 }}'
        speed_threshold: 8
        max_speed_gain: 4
      repeat_delay: 0
    tap_action:
      action: perform-action
      perform_action: samsung_pointer.click
      target:
        entity_id: remote.living_room_tv_pointer
      data:
        button: left
    hold_action:
      action: none
    double_tap_action:
      action: none
    up: {}
    down: {}
    left: {}
    right: {}
```

The gain of `2`, speed threshold of `8`, and maximum speed gain of `4` are starting
values, not measured latency guarantees. The speed threshold is the input distance
per touch sample at which acceleration starts; increase it for more precise
control, or set `max_speed_gain: 1` to disable acceleration. With `repeat_delay: 0`,
the card forwards each pointer sample. The integration accumulates fractional
movement and combines samples while a TV send is in progress, without building an
unbounded queue. Verify movement and single-tap clicks on a harmless screen before
normal use. Directional swipe actions are disabled when drag mode is enabled. See the
[Universal Remote Card documentation](https://github.com/Nerwyn/universal-remote-card#touchpad-interactions).

## Pairing, updates and troubleshooting

- If pairing times out, check TV connection permissions and keep its screen on.
  Successful HTTP access or WebSocket handshake alone does not prove authorization.
- If the TV forgets its permission, use the integration's **Reconfigure** menu.
  Reconfiguration verifies the same TV identity before saving the new address/token.
- Existing SamsungTV Smart entities, apps, volume and power controls are untouched.
- Commands are bounded; movement samples arriving during a send are coalesced into
  one bounded movement. Failed sends are not retried because the TV may have already
  acted on them.
- Protocol support can vary by firmware and app. No fallback silently substitutes
  arrow keys for pointer movements.
- To remove: delete the pointer element from your dashboard, remove Samsung Pointer
  from Devices & services, then uninstall it in HACS and restart HA. You can also
  remove its permission from the TV's connected-device list.

## Privacy and connection security

TV addresses and pairing tokens are entered locally and stored by Home Assistant
in the config entry. They are not part of this repository or diagnostics. Never
publish your HA config files. The integration does not log token-bearing URLs or
raw TV replies and does not read another integration's credentials.

Samsung's local WebSocket uses a self-signed certificate. Certificate validation
is disabled only for this TV connection, as required by this protocol; use a
trusted local network. No external relay or always-on computer is required.

## Development

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q
npm test
```

Tests use a simulated local TV and an isolated HA instance. They never operate a
real television. Tests cannot establish pointer support in an actual TV app.

## Credits and license

Independent implementation of the local protocol used by
[SamsungTV Smart](https://github.com/ollo69/ha-samsungtv-smart) and
[samsung-tv-ws-api](https://github.com/xchwarze/samsung-tv-ws-api).
No source code is copied from those implementations. Not affiliated with Samsung.

MIT license. See [LICENSE](LICENSE).

### Icon-only trackpad switch (v0.2.1)

Set `compact: true` on `custom:samsung-pointer-card` to hide the heading,
connection controls, sensitivity slider, and all visible status/help text. The
configured sensitivity and interval still apply. The pad retains accessible
labels and a status tooltip. If disconnected, the first touch connects; repeat
the gesture once connected.

`custom:samsung-pointer-switch-card` takes two complete card configurations:
`off_card` (legacy pad/remote) and `on_card` (a vertical stack containing the
compact precision pad and the other remote buttons). Its cursor-icon button
switches between them and lights up for the precision mode. It starts off on
page load; mode is local to this card instance, with no helper or automation.
Switching removes the previous card, clearing unsent precision-pad movements.
Both modes receive the current HA state through the standard card helpers.

Update the existing module resource URL to
`/samsung_pointer/samsung-pointer-card.js?v=0.2.1` and reload the dashboard after
installing the update. Card-only file changes on an already running v0.2.0
installation do not require an HA restart.

The mode button is overlaid inside the top-right corner of the trackpad. Use pad-only cards for `off_card` and `on_card`; place shared remote buttons outside the switch in a vertical stack.
