# UDI Polyglot v3 SolarEdge Poly

[![license](https://img.shields.io/github/license/mashape/apistatus.svg)](https://github.com/UniversalDevicesInc/udi-solaredge-poly/blob/master/LICENSE)

This Poly provides an interface between SolarEdge devices and [Polyglot v3](https://github.com/UniversalDevicesInc/pg3) server.

### Installation instructions
1. Got to the Polyglot Store and click "Install" 
2. After the install completes, go to the dashboard and select SolarEdge details
3. Enter your API Key in the custom parameters and save changes.

Nodes should now be created on the ISY that represent your site configuration.

### Configuration

#### Short Poll
 *  How often to poll the SolarEdge servers for inverter/battery/power/energy info. Default
 is 900 seconds (15 minutes)

#### Long Poll
 * How often to poll for Overview Energy Default is 1800 seconds (30 minutes)

#### api_key
 * Your SolarEdge API key.  Get this by logging into your SolarEdge account.

### Notes

Please report any problems on the UDI user forum.

This solaredge version is based on xKing and bpwwer's version. Added are an Energy Node which uses the most recent 15min period to show energy (KWh). Since these readings will always be 15 minutes you can get a more accurate average power (KW) for that period by multiplying the energy figure by 4.
Note that the API only updates values every 5 minutes so the added "since last update" field shows how long ago the power reading was taken. The EnergyDay node shows todays energy. The Overview node shows the Energy history. It is updated during the long poll interval as most of the fields change very slowly.

