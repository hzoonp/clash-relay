# Download isolation: client acceptance

The generated Mihomo profile exposes the normal mixed port at `127.0.0.1:7890` and the download-only mixed listener at `127.0.0.1:7891` (`download-in`). The listener has `proxy: 下载流量`, so its route does not depend on ordinary rules. Download classifiers on the normal/TUN path have adjacent `REJECT` terminal guards for unsupported UDP. Generic ProxyLite traffic uses General. The production proof reports a passed **configuration guarantee** only after emitted-rule and graph audits. Its **deployment guarantee** stays `unverified` until the actual client workflow is checked.

## Desktop / FlClash

1. Load the generated profile in the target client and confirm the core accepts the `download-in` listener. Check that both local ports are available.
2. Configure the download manager to use HTTP/SOCKS proxy `127.0.0.1:7891`. If using a browser handoff, confirm that the downloaded file is requested by the manager or through that port; a browser click alone does not prove handoff.
3. With the client connection view or Mihomo connection logs, download a test file. Record the listener-bound target or matched process rule, `下载流量`, and selected source family. Do not record the file URL, hostname, node name or subscription URL in public artifacts.
4. Repeat with a download from a domain that also matches an ordinary web or AI rule. The download must retain the listener or process route. Ordinary Google/GitHub pages may match ProxyLite, but then use General rather than browsing/SUB_1.
5. Test every application that can initiate downloads. If any application can download over a normal SUB_1 browsing/AI connection without handoff, the deployment-wide guarantee is **not verified** for that client.

## Android / Google Play

Check the actual package/process name reported by the target FlClash/Mihomo mode while Play installs or updates an app. Confirm `com.android.vending` is the originating process and `下载流量` is selected, including a connection to a GoogleCN domain. If Play services or another package originates the transfer, add and test its exact process rule or route that traffic through a controlled download entry. Until this is observed on the target device, mark Android process isolation `unverified`.

## Acceptance record

Keep a private record with the client version, operating system, Mihomo version, tested download applications, rule/target outcome, and whether all download-capable applications are forced through a controlled path. The public production proof includes only aggregate configuration results and deliberately does not infer this device-side result.
