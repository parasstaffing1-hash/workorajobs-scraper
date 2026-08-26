"""Probe candidate career URLs for the Nifty 50 companies not yet in config."""
import httpx

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
client = httpx.Client(headers=UA, timeout=20, follow_redirects=True)

CANDIDATES = [
    ("Adani Enterprises", "https://www.adani.com/careers"),
    ("Adani Ports and SEZ", "https://www.adaniports.com/careers"),
    ("Apollo Hospitals Enterprise", "https://www.apollohospitals.com/careers"),
    ("Asian Paints", "https://www.asianpaints.com/careers.html"),
    ("Axis Bank", "https://www.axisbank.com/careers"),
    ("Bajaj Auto", "https://www.bajajauto.com/careers"),
    ("Bajaj Finance", "https://www.bajajfinserv.in/careers"),
    ("Bajaj Finserv", "https://www.bajajfinserv.in/careers"),
    ("Bharti Airtel", "https://www.airtel.in/careers"),
    ("Britannia Industries", "https://www.britannia.co.in/careers"),
    ("Cipla", "https://www.cipla.com/careers"),
    ("Coal India", "https://www.coalindia.in/careers"),
    ("Divis Laboratories", "https://www.divislabs.com/careers"),
    ("Dr Reddys Laboratories", "https://careers.drreddys.com"),
    ("Eicher Motors", "https://www.eicher.in/careers"),
    ("Grasim Industries", "https://www.adityabirla.com/careers"),
    ("HCL Technologies", "https://careers.hcltech.com"),
    ("HDFC Bank", "https://careers.hdfcbank.com"),
    ("HDFC Life Insurance", "https://www.hdfclife.com/careers"),
    ("Hero MotoCorp", "https://www.heromotocorp.com/en-in/careers"),
    ("Hindalco Industries", "https://www.hindalco.com/careers"),
    ("Hindustan Unilever", "https://careers.hul.co.in"),
    ("ICICI Bank", "https://www.icicicareers.com"),
    ("ITC", "https://www.itcportal.com/careers"),
    ("IndusInd Bank", "https://www.indusind.com/careers"),
    ("Infosys", "https://careers.infosys.com"),
    ("JSW Steel", "https://www.jsw.in/careers"),
    ("Kotak Mahindra Bank", "https://www.kotak.com/en/careers.html"),
    ("Larsen and Toubro", "https://www.larsentoubro.com/corporate/careers"),
    ("LTIMindtree", "https://careers.ltimindtree.com"),
    ("Mahindra and Mahindra", "https://careers.mahindra.com"),
    ("Maruti Suzuki", "https://www.marutisuzuki.com/careers"),
    ("NTPC", "https://www.ntpc.co.in/en/careers"),
    ("Power Grid Corporation", "https://www.powergrid.in/careers"),
    ("SBI Life Insurance", "https://www.sbilife.co.in/careers"),
    ("Sun Pharmaceutical Industries", "https://www.sunpharma.com/careers"),
    ("Tata Consumer Products", "https://www.tataconsumer.com/careers"),
    ("Tech Mahindra", "https://careers.techmahindra.com"),
    ("Titan Company", "https://www.titancompany.in/careers"),
    ("UltraTech Cement", "https://www.ultratechcement.com/careers"),
    ("UPL", "https://www.upl-ltd.com/careers"),
    ("Wipro", "https://careers.wipro.com"),
]

for name, url in CANDIDATES:
    try:
        r = client.get(url)
        text = r.text.lower()
        markers = []
        for m in ["careers", "jobs", "apply", "jobposting", "vacanc", "hiring"]:
            if m in text[:150000]:
                markers.append(m)
        print(f"{'OK ' if r.status_code == 200 else str(r.status_code):4} {name:28} {url:55} {','.join(markers[:3])}")
    except Exception as e:
        print(f"ERR {name:28} {url:55} {type(e).__name__}: {str(e)[:60]}")
