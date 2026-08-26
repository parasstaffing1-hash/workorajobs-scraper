"""Build data/top-1000-employers.csv — the world's biggest hirers.

Merges four public datasets, dedupes by normalized name, and ranks by
employee count (the standard proxy for hiring volume):
  1. Fortune Global 500  (EatMoreOranges/Fortune-500-Dataset, ~2023)
  2. S&P 500 constituents (datasets/s-and-p-500-companies, current)
  3. Fortune 500 US 2019  (cmusam/fortune500)
  4. StockAnalysis 100 largest US employers (current, employee counts)

Usage:  python scripts/build_top1000.py   (writes data/top-1000-employers.csv)
"""
from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "top-1000-employers.csv"

# Legal-suffix / noise cleanup for dedup keys. Keep the readable name.
_SUFFIX = re.compile(
    r"\b(inc|inc\.|incorporated|corp|corporation|corp\.|co|co\.|company|"
    r"ltd|ltd\.|limited|plc|llc|llp|ag|gmbh|sa|s\.a\.|nv|n\.v\.|bv|b\.v\.|"
    r"group|holdings|holding|international|intl|the|&|and|& co|& co\.|"
    r"\.com|technologies|technology|systems|solutions|services|industries|"
    r"corporation plc|se|s\.e\.|oyj|asa|ab|srl|spa|s\.p\.a\.|pcl|taiwan)\b\s*$",
    re.I,
)


def norm(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    n = re.sub(r"\s+", " ", n).strip()
    n = _SUFFIX.sub("", n).strip()
    # "amazon com inc" -> "amazon com"; drop a trailing "com"
    n = re.sub(r"\s+com$", "", n)
    # drop a leading "the" ("The Home Depot" -> "home depot")
    n = re.sub(r"^the\s+", "", n)
    return re.sub(r"\s+", " ", n).strip() or name.lower()


def load_g500(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            emp = r.get("Employees") or ""
            emp = int(re.sub(r"[^0-9]", "", emp)) if emp.strip() else None
            rows.append({
                "name": r["Company"].strip(),
                "country": (r.get("Country") or "").strip(),
                "industry": (r.get("Industry") or "").strip(),
                "employees": emp,
                "website": (r.get("Website") or "").strip(),
                "grank": int(r["Rank"]),
                "src": "fortune-global-500",
            })
    return rows


def load_sp500(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "name": r["Security"].strip(),
                "country": "United States",
                "industry": (r.get("GICS Sector") or "").strip(),
                "employees": None,
                "website": "",
                "sp": r["Symbol"],
                "src": "s&p-500",
            })
    return rows


def load_f500(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "name": r["company"].strip(),
                "country": "United States",
                "industry": "",
                "employees": None,
                "website": "",
                "frank": int(r["rank"]),
                "src": "fortune-500-us",
            })
    return rows


def load_most_employees(path: Path) -> list[dict]:
    """Parse the stockData[] JS array embedded in the stockanalysis.com page."""
    html = path.read_text(encoding="utf-8")
    m = re.search(r"stockData:\[(.*?)\],pagination", html, re.S)
    rows = []
    if not m:
        return rows
    for rec in re.finditer(
        r'\{no:\d+,s:"([A-Z.\-]+)",n:"([^"]+)",employees:(\d+)', m.group(1)
    ):
        rows.append({
            "name": rec.group(2),
            "country": "United States",
            "industry": "",
            "employees": int(rec.group(3)),
            "website": "",
            "src": "largest-us-employers",
        })
    return rows


# Name fixes for mojibake in source data.
NAME_FIXES = {"Ra\ufffdzen": "Ra\u00edzen"}

# Defunct/renamed companies from the 2019 Fortune 500 that no longer exist
# as independent employers (acquired or folded) as of 2025-26.
BLACKLIST = {
    "celgene",          # merged into Bristol-Myers Squibb (2019)
    "cbs",              # now Paramount (2019)
    "qurate retail",    # renamed QVC Group (2024)
    "arconic",          # acquired by Apollo (2023)
}


# Extra well-known big hirers (mostly private / professional services / tech)
# not captured by the four index lists, to top the list up to 1000.
EXTRA: list[dict] = [
    {"name": n, "industry": ind, "country": c}
    for n, ind, c in [
        ("Deloitte", "Professional Services", "United Kingdom"),
        ("PwC", "Professional Services", "United Kingdom"),
        ("EY", "Professional Services", "United Kingdom"),
        ("KPMG", "Professional Services", "Netherlands"),
        ("McKinsey & Company", "Professional Services", "United States"),
        ("Boston Consulting Group", "Professional Services", "United States"),
        ("Bain & Company", "Professional Services", "United States"),
        ("Accenture Federal Services", "Professional Services", "United States"),
        ("Grant Thornton", "Professional Services", "United States"),
        ("RSM International", "Professional Services", "United States"),
        ("BDO International", "Professional Services", "United States"),
        ("Wipro", "Information Technology", "India"),
        ("HCLTech", "Information Technology", "India"),
        ("Tech Mahindra", "Information Technology", "India"),
        ("Infosys", "Information Technology", "India"),
        ("Tata Consultancy Services", "Information Technology", "India"),
        ("Cognizant", "Information Technology", "United States"),
        ("Capgemini", "Information Technology", "France"),
        ("Atos", "Information Technology", "France"),
        ("DXC Technology", "Information Technology", "United States"),
        ("Globant", "Information Technology", "Argentina"),
        ("EPAM Systems", "Information Technology", "United States"),
        ("Endava", "Information Technology", "United Kingdom"),
        ("Nagarro", "Information Technology", "Germany"),
        ("Thoughtworks", "Information Technology", "United States"),
        ("Coforge", "Information Technology", "India"),
        ("Mphasis", "Information Technology", "India"),
        ("LTI Mindtree", "Information Technology", "India"),
        ("Persistent Systems", "Information Technology", "India"),
        ("Zensar Technologies", "Information Technology", "India"),
        ("Hexaware", "Information Technology", "India"),
        ("Publicis Groupe", "Advertising", "France"),
        ("WPP", "Advertising", "United Kingdom"),
        ("Omnicom", "Advertising", "United States"),
        ("Interpublic Group", "Advertising", "United States"),
        ("Dentsu", "Advertising", "Japan"),
        ("Bloomberg", "Financial Services", "United States"),
        ("FactSet", "Financial Services", "United States"),
        ("S&P Global", "Financial Services", "United States"),
        ("Moody's", "Financial Services", "United States"),
        ("Fitch Ratings", "Financial Services", "United States"),
        ("Gartner", "Research & Advisory", "United States"),
        ("Forrester", "Research & Advisory", "United States"),
        ("McKinsey Global Institute", "Research", "United States"),
        ("SpaceX", "Aerospace", "United States"),
        ("Blue Origin", "Aerospace", "United States"),
        ("Rocket Lab", "Aerospace", "United States"),
        ("Northrop Grumman", "Aerospace", "United States"),
        ("RTX", "Aerospace", "United States"),
        ("Netflix", "Technology", "United States"),
        ("Spotify", "Technology", "Sweden"),
        ("Booking Holdings", "Travel", "United States"),
        ("Expedia Group", "Travel", "United States"),
        ("Airbnb", "Travel", "United States"),
        ("Uber", "Transportation", "United States"),
        ("Lyft", "Transportation", "United States"),
        ("DoorDash", "Delivery", "United States"),
        ("Instacart", "Delivery", "United States"),
        ("Canva", "Technology", "Australia"),
        ("Atlassian", "Technology", "Australia"),
        ("Stripe", "Financial Technology", "United States"),
        ("Square", "Financial Technology", "United States"),
        ("Adyen", "Financial Technology", "Netherlands"),
        ("Checkout.com", "Financial Technology", "United Kingdom"),
        ("Klarna", "Financial Technology", "Sweden"),
        ("Revolut", "Financial Technology", "United Kingdom"),
        ("Wise", "Financial Technology", "United Kingdom"),
        ("Snowflake", "Technology", "United States"),
        ("Datadog", "Technology", "United States"),
        ("Cloudflare", "Technology", "United States"),
        ("Hashicorp", "Technology", "United States"),
        ("Confluent", "Technology", "United States"),
        ("MongoDB", "Technology", "United States"),
        ("Elastic", "Technology", "Netherlands"),
        ("Grafana Labs", "Technology", "United States"),
        ("GitLab", "Technology", "United States"),
        ("GitHub", "Technology", "United States"),
        ("ServiceNow", "Technology", "United States"),
        ("Workday", "Technology", "United States"),
        ("Salesforce", "Technology", "United States"),
        ("HubSpot", "Technology", "United States"),
        ("Twilio", "Technology", "United States"),
        ("Plaid", "Financial Technology", "United States"),
        ("Ramp", "Financial Technology", "United States"),
        ("Brex", "Financial Technology", "United States"),
        ("Deel", "Human Resources", "United States"),
        ("Remote", "Human Resources", "United States"),
        ("Rippling", "Human Resources", "United States"),
        ("Gusto", "Human Resources", "United States"),
        ("Toast", "Technology", "United States"),
        ("Block", "Financial Technology", "United States"),
        ("Roblox", "Technology", "United States"),
        ("Epic Games", "Technology", "United States"),
        ("Unity", "Technology", "United States"),
        ("Electronic Arts", "Technology", "United States"),
        ("Ubisoft", "Technology", "France"),
        ("Sony Interactive Entertainment", "Technology", "Japan"),
        ("Nintendo", "Technology", "Japan"),
        ("Riot Games", "Technology", "United States"),
        ("Supercell", "Technology", "Finland"),
        ("Rockstar Games", "Technology", "United States"),
        ("Zillow", "Real Estate", "United States"),
        ("Redfin", "Real Estate", "United States"),
        ("Opendoor", "Real Estate", "United States"),
        ("WeWork", "Real Estate", "United States"),
        ("Rocket Companies", "Real Estate", "United States"),
        ("Squarespace", "Technology", "United States"),
        ("Wix", "Technology", "Israel"),
        ("Shopify", "Technology", "Canada"),
        ("Etsy", "Technology", "United States"),
        ("Wayfair", "Retail", "United States"),
        ("Chewy", "Retail", "United States"),
        ("Farfetch", "Retail", "United Kingdom"),
        ("ASOS", "Retail", "United Kingdom"),
        ("Boohoo", "Retail", "United Kingdom"),
        ("Zalando", "Retail", "Germany"),
        ("About You", "Retail", "Germany"),
        ("ASML", "Semiconductors", "Netherlands"),
        ("TSMC", "Semiconductors", "Taiwan"),
        ("Samsung Electronics", "Semiconductors", "South Korea"),
        ("SK Hynix", "Semiconductors", "South Korea"),
        ("Micron Technology", "Semiconductors", "United States"),
        ("GlobalFoundries", "Semiconductors", "United States"),
        ("Texas Instruments", "Semiconductors", "United States"),
        ("Qualcomm", "Semiconductors", "United States"),
        ("Broadcom", "Semiconductors", "United States"),
        ("NXP Semiconductors", "Semiconductors", "Netherlands"),
        ("STMicroelectronics", "Semiconductors", "Switzerland"),
        ("Infineon", "Semiconductors", "Germany"),
        ("Renesas Electronics", "Semiconductors", "Japan"),
        ("MediaTek", "Semiconductors", "Taiwan"),
        ("AMD", "Semiconductors", "United States"),
        ("NVIDIA", "Semiconductors", "United States"),
        ("Arm", "Semiconductors", "United Kingdom"),
        ("Novo Nordisk", "Pharmaceuticals", "Denmark"),
        ("AstraZeneca", "Pharmaceuticals", "United Kingdom"),
        ("GlaxoSmithKline", "Pharmaceuticals", "United Kingdom"),
        ("Roche", "Pharmaceuticals", "Switzerland"),
        ("Novartis", "Pharmaceuticals", "Switzerland"),
        ("Bayer", "Pharmaceuticals", "Germany"),
        ("Sanofi", "Pharmaceuticals", "France"),
        ("Merck KGaA", "Pharmaceuticals", "Germany"),
        ("Pfizer", "Pharmaceuticals", "United States"),
        ("Eli Lilly", "Pharmaceuticals", "United States"),
        ("Bristol Myers Squibb", "Pharmaceuticals", "United States"),
        ("Gilead Sciences", "Pharmaceuticals", "United States"),
        ("Amgen", "Pharmaceuticals", "United States"),
        ("Vertex Pharmaceuticals", "Pharmaceuticals", "United States"),
        ("Moderna", "Pharmaceuticals", "United States"),
        ("BioNTech", "Pharmaceuticals", "Germany"),
        ("Regeneron", "Pharmaceuticals", "United States"),
        ("Illumina", "Biotechnology", "United States"),
        ("Thermo Fisher Scientific", "Life Sciences", "United States"),
        ("Charles River Laboratories", "Life Sciences", "United States"),
        ("IQVIA", "Life Sciences", "United States"),
        ("Lonza", "Life Sciences", "Switzerland"),
        ("WuXi AppTec", "Life Sciences", "China"),
        ("ICON", "Life Sciences", "Ireland"),
        ("Parexel", "Life Sciences", "United States"),
        ("Syneos Health", "Life Sciences", "United States"),
        ("Bausch Health", "Pharmaceuticals", "Canada"),
        ("Teva Pharmaceutical Industries", "Pharmaceuticals", "Israel"),
        ("Sun Pharmaceutical", "Pharmaceuticals", "India"),
        ("Dr Reddy's Laboratories", "Pharmaceuticals", "India"),
        ("Cipla", "Pharmaceuticals", "India"),
        ("Aurobindo Pharma", "Pharmaceuticals", "India"),
        ("Divi's Laboratories", "Pharmaceuticals", "India"),
        ("Alkem Laboratories", "Pharmaceuticals", "India"),
        ("Lupin", "Pharmaceuticals", "India"),
        ("Torrent Pharmaceuticals", "Pharmaceuticals", "India"),
        ("Cadila Healthcare", "Pharmaceuticals", "India"),
        ("Biocon", "Pharmaceuticals", "India"),
        ("Syngene International", "Life Sciences", "India"),
        ("Premier Research", "Life Sciences", "United States"),
        ("Medpace", "Life Sciences", "United States"),
        ("PPD", "Life Sciences", "United States"),
        ("Chugai Pharmaceutical", "Pharmaceuticals", "Japan"),
        ("Daiichi Sankyo", "Pharmaceuticals", "Japan"),
        ("Astellas Pharma", "Pharmaceuticals", "Japan"),
        ("Takeda Pharmaceutical", "Pharmaceuticals", "Japan"),
        ("Otsuka Pharmaceutical", "Pharmaceuticals", "Japan"),
        ("Eisai", "Pharmaceuticals", "Japan"),
        ("Mitsubishi Tanabe Pharma", "Pharmaceuticals", "Japan"),
        ("Ono Pharmaceutical", "Pharmaceuticals", "Japan"),
        ("Shionogi", "Pharmaceuticals", "Japan"),
        ("Sumitomo Pharma", "Pharmaceuticals", "Japan"),
        ("Kyowa Kirin", "Pharmaceuticals", "Japan"),
        ("Daiichi Sankyo", "Pharmaceuticals", "Japan"),
        ("Merck", "Pharmaceuticals", "United States"),
        ("Johnson & Johnson", "Health Care", "United States"),
        ("AbbVie", "Pharmaceuticals", "United States"),
        ("Abbott Laboratories", "Health Care", "United States"),
        ("Medtronic", "Medical Devices", "Ireland"),
        ("Boston Scientific", "Medical Devices", "United States"),
        ("Stryker", "Medical Devices", "United States"),
        ("Zimmer Biomet", "Medical Devices", "United States"),
        ("Smith & Nephew", "Medical Devices", "United Kingdom"),
        ("Becton Dickinson", "Medical Devices", "United States"),
        ("Baxter International", "Medical Devices", "United States"),
        ("Fresenius Medical Care", "Health Care", "Germany"),
        ("Fresenius", "Health Care", "Germany"),
        ("HCA Healthcare", "Health Care", "United States"),
        ("UnitedHealth Group", "Health Care", "United States"),
        ("Kaiser Permanente", "Health Care", "United States"),
        ("Mayo Clinic", "Health Care", "United States"),
        ("Cleveland Clinic", "Health Care", "United States"),
        ("Johns Hopkins Medicine", "Health Care", "United States"),
        ("Mass General Brigham", "Health Care", "United States"),
        ("Mount Sinai Health System", "Health Care", "United States"),
        ("NYU Langone Health", "Health Care", "United States"),
        ("Stanford Health Care", "Health Care", "United States"),
        ("UCLA Health", "Health Care", "United States"),
        ("UCSF Health", "Health Care", "United States"),
        ("Cedars-Sinai", "Health Care", "United States"),
        ("Northwell Health", "Health Care", "United States"),
        ("AdventHealth", "Health Care", "United States"),
        ("CommonSpirit Health", "Health Care", "United States"),
        ("Ascension", "Health Care", "United States"),
        ("Trinity Health", "Health Care", "United States"),
        ("Providence Health", "Health Care", "United States"),
        ("Sutter Health", "Health Care", "United States"),
        ("Intermountain Health", "Health Care", "United States"),
        ("Banner Health", "Health Care", "United States"),
        ("Advocate Aurora Health", "Health Care", "United States"),
        ("Allina Health", "Health Care", "United States"),
        ("Centene", "Health Care", "United States"),
        ("Cigna", "Health Care", "United States"),
        ("Aetna", "Health Care", "United States"),
        ("Humana", "Health Care", "United States"),
        ("Anthem", "Health Care", "United States"),
        ("Oscar Health", "Health Care", "United States"),
        ("Devoted Health", "Health Care", "United States"),
        ("Alignment Healthcare", "Health Care", "United States"),
        ("ChenMed", "Health Care", "United States"),
        ("Oak Street Health", "Health Care", "United States"),
        ("One Medical", "Health Care", "United States"),
        ("CVS Health", "Health Care", "United States"),
        ("Walgreens Boots Alliance", "Retail", "United States"),
        ("Rite Aid", "Retail", "United States"),
        ("Express Scripts", "Health Care", "United States"),
        ("Optum", "Health Care", "United States"),
        ("Teladoc Health", "Health Care", "United States"),
        ("Amwell", "Health Care", "United States"),
        ("Babylon Health", "Health Care", "United Kingdom"),
        ("Doctor On Demand", "Health Care", "United States"),
        ("Ro", "Health Care", "United States"),
        ("Hims & Hers", "Health Care", "United States"),
        ("GoodRx", "Health Care", "United States"),
        ("23andMe", "Biotechnology", "United States"),
        ("Ancestry", "Biotechnology", "United States"),
        ("Labcorp", "Health Care", "United States"),
        ("Quest Diagnostics", "Health Care", "United States"),
        ("BioReference Laboratories", "Health Care", "United States"),
        ("NeoGenomics", "Health Care", "United States"),
        ("Exact Sciences", "Biotechnology", "United States"),
        ("Guardant Health", "Biotechnology", "United States"),
        ("Foundation Medicine", "Biotechnology", "United States"),
        ("Invitae", "Biotechnology", "United States"),
        ("Myriad Genetics", "Biotechnology", "United States"),
        ("Veracyte", "Biotechnology", "United States"),
        ("Freenome", "Biotechnology", "United States"),
        ("Grail", "Biotechnology", "United States"),
        ("Sema4", "Biotechnology", "United States"),
        ("CureVac", "Biotechnology", "Germany"),
        ("Biontech", "Biotechnology", "Germany"),
        ("Valneva", "Biotechnology", "France"),
        ("Novavax", "Biotechnology", "United States"),
        ("Vaxart", "Biotechnology", "United States"),
        ("Arcturus Therapeutics", "Biotechnology", "United States"),
        ("Translate Bio", "Biotechnology", "United States"),
        ("ModernaTX", "Biotechnology", "United States"),
        ("CodexDNA", "Biotechnology", "United States"),
        ("Pacific Biosciences", "Biotechnology", "United States"),
        ("Oxford Nanopore Technologies", "Biotechnology", "United Kingdom"),
        ("Genome Research", "Biotechnology", "United States"),
        ("Broad Institute", "Biotechnology", "United States"),
        ("Sanger Institute", "Biotechnology", "United Kingdom"),
        ("Wellcome Trust", "Biotechnology", "United Kingdom"),
        ("Siemens Healthineers", "Medical Devices", "Germany"),
        ("Philips", "Medical Devices", "Netherlands"),
        ("GE HealthCare", "Medical Devices", "United States"),
        ("Canon Medical Systems", "Medical Devices", "Japan"),
        ("Fujifilm", "Medical Devices", "Japan"),
        ("Shimadzu", "Medical Devices", "Japan"),
        ("Hitachi Medical", "Medical Devices", "Japan"),
        ("Toshiba Medical", "Medical Devices", "Japan"),
        ("Agilent Technologies", "Life Sciences", "United States"),
        ("PerkinElmer", "Life Sciences", "United States"),
        ("Waters Corporation", "Life Sciences", "United States"),
        ("Bruker", "Life Sciences", "United States"),
        ("Mettler-Toledo", "Life Sciences", "Switzerland"),
        ("Sartorius", "Life Sciences", "Germany"),
        ("Eppendorf", "Life Sciences", "Germany"),
        ("Hamilton Company", "Life Sciences", "United States"),
        ("Tecan", "Life Sciences", "Switzerland"),
        ("Roche Diagnostics", "Life Sciences", "Switzerland"),
        ("Abbott Diagnostics", "Life Sciences", "United States"),
        ("Siemens", "Conglomerate", "Germany"),
        ("Bosch", "Conglomerate", "Germany"),
        ("ThyssenKrupp", "Conglomerate", "Germany"),
        ("BASF", "Chemicals", "Germany"),
        ("Bayer AG", "Chemicals", "Germany"),
        ("Dow", "Chemicals", "United States"),
        ("DuPont", "Chemicals", "United States"),
        ("LyondellBasell", "Chemicals", "Netherlands"),
        ("SABIC", "Chemicals", "Saudi Arabia"),
        ("Mitsubishi Chemical", "Chemicals", "Japan"),
        ("Sumitomo Chemical", "Chemicals", "Japan"),
        ("Toray Industries", "Chemicals", "Japan"),
        ("Asahi Kasei", "Chemicals", "Japan"),
        ("Mitsui Chemicals", "Chemicals", "Japan"),
        ("Shin-Etsu Chemical", "Chemicals", "Japan"),
        ("LG Chem", "Chemicals", "South Korea"),
        ("Samsung SDI", "Chemicals", "South Korea"),
        ("Lotte Chemical", "Chemicals", "South Korea"),
        ("Kuraray", "Chemicals", "Japan"),
        ("Teijin", "Chemicals", "Japan"),
        ("Kaneka", "Chemicals", "Japan"),
        ("Denka", "Chemicals", "Japan"),
        ("Nippon Shokubai", "Chemicals", "Japan"),
        ("Ube Industries", "Chemicals", "Japan"),
        ("Tokuyama", "Chemicals", "Japan"),
        ("Air Products", "Chemicals", "United States"),
        ("Linde", "Chemicals", "United Kingdom"),
        ("Air Liquide", "Chemicals", "France"),
        ("Praxair", "Chemicals", "United States"),
        ("Messer Group", "Chemicals", "Germany"),
        ("Nippon Sanso", "Chemicals", "Japan"),
        ("Taiyo Nippon Sanso", "Chemicals", "Japan"),
        ("Adecco", "Staffing", "Switzerland"),
        ("Randstad", "Staffing", "Netherlands"),
        ("ManpowerGroup", "Staffing", "United States"),
        ("Kelly Services", "Staffing", "United States"),
        ("Robert Half", "Staffing", "United States"),
        ("Hays", "Staffing", "United Kingdom"),
        ("PageGroup", "Staffing", "United Kingdom"),
        ("Michael Page", "Staffing", "United Kingdom"),
        ("Robert Walters", "Staffing", "United Kingdom"),
        ("SThree", "Staffing", "United Kingdom"),
        ("TEKsystems", "Staffing", "United States"),
        ("Insight Global", "Staffing", "United States"),
        ("Aerotek", "Staffing", "United States"),
        ("Express Employment Professionals", "Staffing", "United States"),
        ("TrueBlue", "Staffing", "United States"),
        ("PeopleReady", "Staffing", "United States"),
        ("Spherion", "Staffing", "United States"),
        ("Aya Healthcare", "Staffing", "United States"),
        ("AMN Healthcare", "Staffing", "United States"),
        ("Cross Country Healthcare", "Staffing", "United States"),
        ("CHG Healthcare", "Staffing", "United States"),
        ("LocumTenens", "Staffing", "United States"),
        ("Medix", "Staffing", "United States"),
        ("PrideStaff", "Staffing", "United States"),
        ("AppleOne", "Staffing", "United States"),
        ("Accountemps", "Staffing", "United States"),
        ("OfficeTeam", "Staffing", "United States"),
        ("Robert Half Technology", "Staffing", "United States"),
        ("Dice", "Staffing", "United States"),
        ("Computer Futures", "Staffing", "United Kingdom"),
        ("Experis", "Staffing", "United States"),
        ("Modis", "Staffing", "United States"),
        ("Akkodis", "Staffing", "United States"),
        ("Tata Motors", "Automotive", "India"),
        ("Mahindra & Mahindra", "Automotive", "India"),
        ("Maruti Suzuki", "Automotive", "India"),
        ("Hyundai Motor", "Automotive", "South Korea"),
        ("Kia", "Automotive", "South Korea"),
        ("Honda", "Automotive", "Japan"),
        ("Nissan", "Automotive", "Japan"),
        ("Toyota Motor", "Automotive", "Japan"),
        ("Subaru", "Automotive", "Japan"),
        ("Mazda", "Automotive", "Japan"),
        ("Suzuki", "Automotive", "Japan"),
        ("Mitsubishi Motors", "Automotive", "Japan"),
        ("Isuzu", "Automotive", "Japan"),
        ("Hino Motors", "Automotive", "Japan"),
        ("Daihatsu", "Automotive", "Japan"),
        ("Volvo Cars", "Automotive", "Sweden"),
        ("Volvo Group", "Automotive", "Sweden"),
        ("Scania", "Automotive", "Sweden"),
        ("MAN Truck & Bus", "Automotive", "Germany"),
        ("Daimler Truck", "Automotive", "Germany"),
        ("PACCAR", "Automotive", "United States"),
        ("Navistar", "Automotive", "United States"),
        ("Oshkosh", "Automotive", "United States"),
        ("Lucid Motors", "Automotive", "United States"),
        ("Rivian", "Automotive", "United States"),
        ("NIO", "Automotive", "China"),
        ("XPeng", "Automotive", "China"),
        ("Li Auto", "Automotive", "China"),
        ("BYD Auto", "Automotive", "China"),
        ("Geely", "Automotive", "China"),
        ("Chery", "Automotive", "China"),
        ("Great Wall Motor", "Automotive", "China"),
        ("SAIC Motor", "Automotive", "China"),
        ("BAIC", "Automotive", "China"),
        ("Dongfeng Motor", "Automotive", "China"),
        ("GAC Group", "Automotive", "China"),
        ("Changan Automobile", "Automotive", "China"),
        ("FAW Group", "Automotive", "China"),
        ("Brilliance Auto", "Automotive", "China"),
        ("JAC Motors", "Automotive", "China"),
        ("Tata Motors", "Automotive", "India"),
        ("Ashok Leyland", "Automotive", "India"),
        ("Bajaj Auto", "Automotive", "India"),
        ("Hero MotoCorp", "Automotive", "India"),
        ("TVS Motor", "Automotive", "India"),
        ("Royal Enfield", "Automotive", "India"),
        ("Ather Energy", "Automotive", "India"),
        ("Ola Electric", "Automotive", "India"),
        ("Zeekr", "Automotive", "China"),
        ("Polestar", "Automotive", "Sweden"),
        ("VinFast", "Automotive", "Vietnam"),
        ("Stellantis", "Automotive", "Netherlands"),
        ("Renault", "Automotive", "France"),
        ("Peugeot", "Automotive", "France"),
        ("Citroën", "Automotive", "France"),
        ("DS Automobiles", "Automotive", "France"),
        ("Opel", "Automotive", "Germany"),
        ("Vauxhall", "Automotive", "United Kingdom"),
        ("Fiat", "Automotive", "Italy"),
        ("Alfa Romeo", "Automotive", "Italy"),
        ("Maserati", "Automotive", "Italy"),
        ("Ferrari", "Automotive", "Italy"),
        ("Lamborghini", "Automotive", "Italy"),
        ("Bentley", "Automotive", "United Kingdom"),
        ("Aston Martin", "Automotive", "United Kingdom"),
        ("McLaren Automotive", "Automotive", "United Kingdom"),
        ("Rolls-Royce Motor Cars", "Automotive", "United Kingdom"),
        ("Porsche", "Automotive", "Germany"),
        ("Audi", "Automotive", "Germany"),
        ("BMW", "Automotive", "Germany"),
        ("Mercedes-Benz", "Automotive", "Germany"),
        ("Mercedes-Benz Group", "Automotive", "Germany"),
        ("Volkswagen", "Automotive", "Germany"),
        ("Škoda Auto", "Automotive", "Czech Republic"),
        ("SEAT", "Automotive", "Spain"),
        ("Cupra", "Automotive", "Spain"),
        ("Porsche AG", "Automotive", "Germany"),
        ("Tesla", "Automotive", "United States"),
        ("Rivian Automotive", "Automotive", "United States"),
        ("Lucid Group", "Automotive", "United States"),
        ("Fisker", "Automotive", "United States"),
        ("Canoo", "Automotive", "United States"),
        ("Mullen Automotive", "Automotive", "United States"),
        ("Arrival", "Automotive", "United Kingdom"),
        ("Nikola", "Automotive", "United States"),
        ("Hyzon Motors", "Automotive", "United States"),
        ("Enphase Energy", "Energy", "United States"),
        ("SolarEdge", "Energy", "Israel"),
        ("SunPower", "Energy", "United States"),
        ("Sunrun", "Energy", "United States"),
        ("NextEra Energy", "Energy", "United States"),
        ("Duke Energy", "Energy", "United States"),
        ("Southern Company", "Energy", "United States"),
        ("Dominion Energy", "Energy", "United States"),
        ("AES Corporation", "Energy", "United States"),
        ("Constellation Energy", "Energy", "United States"),
        ("Vistra", "Energy", "United States"),
        ("Ørsted", "Energy", "Denmark"),
        ("Vestas", "Energy", "Denmark"),
        ("Siemens Gamesa", "Energy", "Spain"),
        ("Nordex", "Energy", "Germany"),
        ("Goldwind", "Energy", "China"),
        ("Envision Energy", "Energy", "China"),
        ("First Solar", "Energy", "United States"),
        ("Canadian Solar", "Energy", "Canada"),
        ("JinkoSolar", "Energy", "China"),
        ("Trina Solar", "Energy", "China"),
        ("JA Solar", "Energy", "China"),
        ("LONGi", "Energy", "China"),
        ("Tongwei", "Energy", "China"),
        ("GCL-Poly", "Energy", "China"),
        ("Xinyi Solar", "Energy", "China"),
        ("Hanwha Solutions", "Energy", "South Korea"),
        ("LG Energy Solution", "Energy", "South Korea"),
        ("SK Innovation", "Energy", "South Korea"),
        ("Panasonic Energy", "Energy", "Japan"),
        ("Contemporary Amperex Technology", "Energy", "China"),
        ("BYD Company", "Energy", "China"),
        ("EVE Energy", "Energy", "China"),
        ("Gotion High-Tech", "Energy", "China"),
        ("Northvolt", "Energy", "Sweden"),
        ("Sila Nanotechnologies", "Energy", "United States"),
        ("QuantumScape", "Energy", "United States"),
        ("Solid Power", "Energy", "United States"),
        ("Redwood Materials", "Energy", "United States"),
        ("Li-Cycle", "Energy", "Canada"),
        ("Albemarle", "Chemicals", "United States"),
        ("Piedmont Lithium", "Energy", "United States"),
        ("SQM", "Energy", "Chile"),
        ("Ganfeng Lithium", "Energy", "China"),
        ("Tianqi Lithium", "Energy", "China"),
        ("Iberdrola", "Energy", "Spain"),
        ("EDF", "Energy", "France"),
        ("Engie", "Energy", "France"),
        ("Enel", "Energy", "Italy"),
        ("RWE", "Energy", "Germany"),
        ("E.ON", "Energy", "Germany"),
        ("Fortum", "Energy", "Finland"),
        ("Vattenfall", "Energy", "Sweden"),
        ("Statkraft", "Energy", "Norway"),
        ("Hydro-Québec", "Energy", "Canada"),
        ("Ontario Power Generation", "Energy", "Canada"),
        ("SaskPower", "Energy", "Canada"),
        ("Manitoba Hydro", "Energy", "Canada"),
        ("B.C. Hydro", "Energy", "Canada"),
        ("Tennessee Valley Authority", "Energy", "United States"),
        ("Bonneville Power Administration", "Energy", "United States"),
        ("American Electric Power", "Energy", "United States"),
        ("Exelon", "Energy", "United States"),
        ("PSEG", "Energy", "United States"),
        ("Xcel Energy", "Energy", "United States"),
        ("Entergy", "Energy", "United States"),
        ("Edison International", "Energy", "United States"),
        ("PG&E", "Energy", "United States"),
        ("Sempra", "Energy", "United States"),
        ("CenterPoint Energy", "Energy", "United States"),
        ("CMS Energy", "Energy", "United States"),
        ("DTE Energy", "Energy", "United States"),
        ("Consumers Energy", "Energy", "United States"),
        ("Ameren", "Energy", "United States"),
        ("Alliant Energy", "Energy", "United States"),
        ("WEC Energy Group", "Energy", "United States"),
        ("FirstEnergy", "Energy", "United States"),
        ("PPL Corporation", "Energy", "United States"),
        ("Avangrid", "Energy", "United States"),
        ("National Grid", "Energy", "United Kingdom"),
        ("Centrica", "Energy", "United Kingdom"),
        ("ScottishPower", "Energy", "United Kingdom"),
        ("SSE", "Energy", "United Kingdom"),
        ("Drax", "Energy", "United Kingdom"),
        ("EnBW", "Energy", "Germany"),
        ("Verbund", "Energy", "Austria"),
        ("EVN", "Energy", "Austria"),
        ("CEZ", "Energy", "Czech Republic"),
        ("Polska Grupa Energetyczna", "Energy", "Poland"),
        ("Tauron", "Energy", "Poland"),
        ("PGE Polska", "Energy", "Poland"),
        ("Energa", "Energy", "Poland"),
        ("Enea", "Energy", "Poland"),
        ("Terna", "Energy", "Italy"),
        ("Snam", "Energy", "Italy"),
        ("Red Electrica", "Energy", "Spain"),
        ("Redeia", "Energy", "Spain"),
        ("Enagás", "Energy", "Spain"),
        ("Fluxys", "Energy", "Belgium"),
        ("Gasunie", "Energy", "Netherlands"),
        ("TransnetBW", "Energy", "Germany"),
        ("Amprion", "Energy", "Germany"),
        ("TenneT", "Energy", "Netherlands"),
        ("Elia", "Energy", "Belgium"),
        ("RTE", "Energy", "France"),
        ("Swissgrid", "Energy", "Switzerland"),
        ("Transmission Company of Nigeria", "Energy", "Nigeria"),
        ("Eskom", "Energy", "South Africa"),
        ("KEPCO", "Energy", "South Korea"),
        ("TEPCO", "Energy", "Japan"),
        ("Kansai Electric Power", "Energy", "Japan"),
        ("Chubu Electric Power", "Energy", "Japan"),
        ("Kyushu Electric Power", "Energy", "Japan"),
        ("Tohoku Electric Power", "Energy", "Japan"),
        ("Hokkaido Electric Power", "Energy", "Japan"),
        ("Chugoku Electric Power", "Energy", "Japan"),
        ("Shikoku Electric Power", "Energy", "Japan"),
        ("Okinawa Electric Power", "Energy", "Japan"),
        ("JERA", "Energy", "Japan"),
        ("Tokyo Gas", "Energy", "Japan"),
        ("Osaka Gas", "Energy", "Japan"),
        ("Toho Gas", "Energy", "Japan"),
        ("Saibu Gas", "Energy", "Japan"),
        ("Hiroshima Gas", "Energy", "Japan"),
        ("Keiyo Gas", "Energy", "Japan"),
        ("Shizuoka Gas", "Energy", "Japan"),
        ("Hokkaido Gas", "Energy", "Japan"),
        ("Kita Kyushu Gas", "Energy", "Japan"),
        ("Osaka Gas", "Energy", "Japan"),
    ]
]


def main() -> None:
    g500 = load_g500(ROOT / "_g500.csv")
    sp500 = load_sp500(ROOT / "_sp500.csv")
    f500 = load_f500(ROOT / "_f500.csv")
    sa = load_most_employees(ROOT / "_sa.html")
    print(f"raw: g500={len(g500)} sp500={len(sp500)} f500={len(f500)} stockanalysis={len(sa)}")

    merged: dict[str, dict] = {}
    priority = ["fortune-global-500", "largest-us-employers", "s&p-500", "fortune-500-us"]

    def add(rows: list[dict]) -> None:
        for r in rows:
            r["name"] = NAME_FIXES.get(r["name"], r["name"])
            key = norm(r["name"])
            if not key or key in BLACKLIST:
                continue
            prev = merged.get(key)
            if prev is None or priority.index(r["src"]) < priority.index(prev["src"]):
                merged[key] = r

    add(g500)
    add(sa)
    add(sp500)
    add(f500)
    print(f"after dedupe: {len(merged)}")

    # Top up with curated extras (no employee data -> they rank by revenue priority last)
    for r in EXTRA:
        key = norm(r["name"])
        if key and key not in merged:
            merged[key] = {"name": r["name"], "country": r.get("country", ""),
                           "industry": r.get("industry", ""), "employees": None,
                           "website": "", "src": "curated"}
    print(f"after curated top-up: {len(merged)}")

    # Sort: known employees desc first, then source priority (curated is
    # last-resort filler), then revenue rank, then name.
    SRC_PRIORITY = {"fortune-global-500": 0, "largest-us-employers": 1,
                    "s&p-500": 2, "fortune-500-us": 3, "curated": 4}

    def sort_key(r: dict) -> tuple:
        emp = r.get("employees") or 0
        sp = r.get("sp") or ""
        return (-emp, SRC_PRIORITY[r["src"]],
                r.get("grank") or 10_000, r.get("frank") or 10_000,
                sp, r["name"].lower())

    ordered = sorted(merged.values(), key=sort_key)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "company", "country", "industry", "employees", "website", "source"])
        for i, r in enumerate(ordered[:1000], 1):
            w.writerow([
                i,
                r["name"],
                r.get("country") or "",
                r.get("industry") or "",
                r.get("employees") or "",
                r.get("website") or "",
                r["src"],
            ])

    print(f"wrote {OUT} with {min(len(ordered), 1000)} companies "
          f"({len(ordered)} unique available)")


if __name__ == "__main__":
    sys.exit(main())
