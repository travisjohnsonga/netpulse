# HPE AOS-CX MIBs

Download from the HPE/Aruba support portal: https://asp.arubanetworks.com/downloads
(search for "AOS-CX MIB"). Common files:
- `ARUBAWIRED-CHASSIS-MIB.mib`
- `ARUBAWIRED-INTERFACE-MIB.mib`
- `ARUBAWIRED-SYSTEM-MIB.mib`

AOS-CX exposes CPU/memory via the standard HOST-RESOURCES-MIB
(`hrProcessorLoad`), so NetPulse works without the vendor MIBs — they only add
human-readable names for AOS-CX-specific OIDs. Place downloaded files here.
