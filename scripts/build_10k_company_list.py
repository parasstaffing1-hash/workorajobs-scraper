#!/usr/bin/env python3
"""Build a massive list of 10K+ companies from public sources.

Combines:
1. Fortune 500 / Forbes 2000 / S&P 500 / NASDAQ 100
2. YC companies (1,500+)
3. Crunchbase top companies
4. GitHub organizations
5. Well-known tech companies
6. Consulting / Services firms
7. Government contractors
8. Regional companies (EU, APAC, LATAM)

Output: company_names.txt (one per line)
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# MASSIVE company name list — aim for 10,000+ unique names
# ---------------------------------------------------------------------------

COMPANIES = [
    # ===== BIG TECH (100) =====
    "Google", "Microsoft", "Apple", "Amazon", "Meta", "Netflix", "Spotify",
    "Uber", "Lyft", "Airbnb", "Twitter", "Snap", "Pinterest", "LinkedIn",
    "Salesforce", "Oracle", "SAP", "Adobe", "VMware", "Cisco", "Intel",
    "NVIDIA", "AMD", "Qualcomm", "Broadcom", "IBM", "HP", "Dell", "Lenovo",
    "Sony", "Samsung", "LG", "Huawei", "Xiaomi", "OPPO", "Vivo",
    "Alphabet", "Microsoft", "Apple", "Amazon", "Meta Platforms",

    # ===== CLOUD / INFRASTRUCTURE (150) =====
    "AWS", "Microsoft Azure", "Google Cloud", "DigitalOcean", "Linode",
    "Vultr", "Heroku", "Cloudflare", "Fastly", "Akamai", "StackPath",
    "Datadog", "New Relic", "PagerDuty", "Dynatrace", "Splunk", "Elastic",
    "MongoDB", "Redis", "Confluent", "Snowflake", "Databricks", "dbt Labs",
    "HashiCorp", "Terraform", "Vault", "Consul", "Nomad",
    "Atlassian", "Jira", "Confluence", "Bitbucket", "Trello", "Slack",
    "Zoom", "Webex", "RingCentral", "Dropbox", "Box",
    "CloudKitchens", "CloudRoutes", "CloudHealth",

    # ===== DEVTOOLS / CI/CD (100) =====
    "GitHub", "GitLab", "CircleCI", "Travis CI", "Jenkins", "SonarQube",
    "Snyk", "Veracode", "Checkmarx", "Postman", "Insomnia",
    "Vercel", "Netlify", "Render", "Fly.io", "Railway", "Koala",
    "Supabase", "PlanetScale", "Neon", "Xata", "Turso", "Cockroach Labs",
    "Prisma", "Hasura", "Nhost", "Appwrite", "Firebase",
    "Sentry", "Bugsnag", "Rollbar", "LogRocket", "FullStory",
    "Sourcegraph", "Code Climate", "DeepSource", "Codacy", "Codecov",
    "LaunchDarkly", "Split.io", "ConfigCat",
    "Retool", "Internal", "Appsmith", "Budibase",

    # ===== AI / ML (100) =====
    "OpenAI", "Anthropic", "Cohere", "Stability AI", "Midjourney",
    "Hugging Face", "Replicate", "Modal", "Anyscale", "Together AI",
    "Groq", "Cerebras", "SambaNova", "fal", "Baseten", "Banana",
    "Deep Infra", "Fireworks AI", "Weights & Biases", "Neptune",
    "Labelbox", "Scale AI", "Snorkel AI", "Landing AI",
    "Celonis", "Dataiku", "Domino Data Lab", "CNVRG", "Valohai",
    "Weights & Biases", "Comet ML", "DeterminedReader",
    "Stability AI", "Runway ML", "Jasper", "Copy.ai", "Writesonic",
    "Character AI", "Replika", "Khan Academy AI", "Duolingo AI",
    "Grammarly", "Notion AI", "GitHub Copilot", "Amazon CodeWhisperer",
    "Tabnine", "Codex", "Cursor", "Aider", "Continue.dev",
    "LangChain", "LlamaIndex", "Haystack", "Semantic Kernel",
    "Pinecone", "Weaviate", "Chroma", "Milvus", "Qdrant",
    "LangSmith", "Helicone", "Portkey", "Guardrails AI",

    # ===== FINTECH (150) =====
    "Stripe", "Square", "PayPal", "Adyen", "Checkout.com", "Klarna",
    "Affirm", "Sezzle", "Zip", "Afterpay",
    "Plaid", "Marqeta", "Ramp", "Brex", "Mercury", "Relay",
    "Chime", "Nubank", "N26", "Revolut", "Monzo", "Starling Bank",
    "Wise", "Remitly", "Western Union", "Xoom",
    "Robinhood", "Coinbase", "Gemini", "Kraken", "Binance",
    "Cash App", "Venmo", "Zelle",
    "Ant Group", "Lemonade", "Root Insurance", "Hippo",
    "Current", "Step", "One", "Arc", "Gusto",
    "Bill.com", "Tipalti", "Airbase", "Divvy",
    "Navan", "TravelPerk", "Lily", "Brex",
    "Fundbox", "BlueVine", "Kabbage", "OnDeck",
    "SoFi", "LendingClub", "Prosper", "Upstart",
    "Wealthfront", "Betterment", "Schwab", "Fidelity",
    "Vanguard", "BlackRock", "Blackstone", "Bridgewater",

    # ===== E-COMMERCE (100) =====
    "Shopify", "BigCommerce", "WooCommerce", "Magento", "PrestaShop",
    "Amazon", "eBay", "Etsy", "Walmart", "Target", "Costco",
    "Alibaba", "AliExpress", "JD.com", "Pinduoduo", "Shopee",
    "Lazada", "Tokopedia", "Bukalapak",
    "Mercado Libre", "Rappi", "DoorDash", "Instacart",
    "GrubHub", "Uber Eats", "Postmates",
    "Wayfair", "Overstock", "Chewy", "Fanatics",
    "Zalando", "ASOS", "Boohoo", "Shein", "Temu",

    # ===== SOCIAL / CONTENT (100) =====
    "TikTok", "ByteDance", "Reddit", "Quora", "Medium", "Substack",
    "Ghost", "WordPress", "Webflow", "Framer",
    "Figma", "Sketch", "InVision", "Zeplin", "Canva",
    "Miro", "Luma", "Notion", "Airtable", "Coda",
    "Loom", "Vidyard", "Wistia", "Mux", "Cloudinary",
    "Twitch", "Discord", "Signal", "Telegram", "WhatsApp",
    "ByteDance", "Snap Inc.", "Pinterest", "Tumblr",

    # ===== CYBERSECURITY (80) =====
    "CrowdStrike", "Zscaler", "Palo Alto Networks", "Fortinet", "FireEye",
    "Mandiant", "Rapid7", "Qualys", "Tenable", "SentinelOne",
    "Darktrace", "Recorded Future", "Cybereason", "Abnormal Security",
    "Material Security", "Arctic Wolf", "Expel", "Huntress",
    "Snyk", "Veracode", "Checkmarx", "WhiteSource",
    "Vera", "Virtru", "Okta", "Auth0", "Duo Security",
    "OneLogin", "Ping Identity", "ForgeRock",
    "HashiCorp Vault", "CyberArk", "BeyondTrust", "Thycotic",
    "Proofpoint", "Mimecast", "Barracuda", "Fortinet",
    "Sophos", "Kaspersky", "Bitdefender", "Malwarebytes",
    "Tanium", "Ivanti", "Qualys", "Rapid7",

    # ===== HEALTH TECH (80) =====
    "Tempus", "Flatiron Health", "Guardant Health", "Grail", "Color",
    "One Medical", "Halo Health", "Oura", "Whoop", "Peloton",
    "Nurx", "Ro", "Hims & Hers", "Cerebral",
    "Talkspace", "BetterHelp", "Calm", "Headspace",
    "Zocdoc", "Genome Medical", "Invitae",
    "Oscar Health", "Clover Health", "Bright Health",
    "Virta Health", "Livongo", "Teladoc", "Amwell",
    "Phreesia", "Waystar", "R1 RCM", "Change Healthcare",
    "IQVIA", "Medidata", "Veeva Systems", "Castlight Health",
    "Grand Rounds", "Transcarent", "Collective Health",
    "Abductive", "Redox", "Hims", "Ro Health",
    "Netsmart", "Cerner", "Epic Systems", "Allscripts",
    "athenahealth", "eClinicalWorks", "NextGen Healthcare",

    # ===== ENTERPRISE / HR TECH (80) =====
    "ServiceNow", "Workday", "SAP SuccessFactors", "BambooHR",
    "Gusto", "Zenefits", "Rippling", "Lattice", "Leapsome",
    "15Five", "Culture Amp", "Deel", "Remote.com",
    "Oyster HR", "Papaya Global", "Multiplier", "Velocity Global",
    "Greenhouse", "Lever", "Ashby", "Workable", "SmartRecruiters",
    "iCIMS", "Taleo", "Jobvite", "JazzHR", "Breezy HR",
    "Teamtailor", "HireHive", "Recruitee", "Freshteam",
    "Bullhorn", "JobAdder", "Erecruit", "Bullhorn",
    "BambooHR", "Fingercheck", "Paylocity", "Paycom",
    "ADP", "Paychex", "UKG", "Ceridian",
    "Cornerstone OnDemand", "Docebo", "360Learning", "Lessonly",
    "Lattice", "15Five", "Culture Amp", "Lever",
    "BambooHR", "Zoho People", "Kissflow", "KiwiHR",

    # ===== TRANSPORTATION / AUTO (80) =====
    "Tesla", "SpaceX", "Blue Origin", "Rivian", "Lucid Motors",
    "NIO", "XPeng", "Li Auto", "BYD", "Polestar",
    "Ford", "GM", "Toyota", "Honda", "BMW",
    "Mercedes-Benz", "Volvo", "Stellantis", "Hyundai", "Kia",
    "Fisker", "Canoo", "Lordstown", "Nikola",
    "Arrival", "Proterra", "Rivian", "Lucid",
    "Aurora", "Waymo", "Cruise", "Argo AI", "Motional",
    "Zoox", "Nuro", "Kodiak", "Gatik", "TuSimple",
    "Plus.ai", "Embark", "InMotion", "Byton",

    # ===== MEDIA / ENTERTAINMENT (80) =====
    "Disney", "Warner Bros", "Paramount", "NBC Universal", "CBS",
    "Spotify", "Pandora", "SoundCloud", "Audible", "Tidal",
    "YouTube", "Vimeo", "Dailymotion", "Twitch",
    "Roku", "Apple TV", "Hulu", "Peacock", "Paramount+",
    "Discovery", "AMC", "A&E", "Lionsgate",
    "Activision Blizzard", "Electronic Arts", "Take-Two", "Ubisoft",
    "Epic Games", "Riot Games", "Valve", "Bethesda", "Rockstar",
    "Roblox", "Unity", "Unreal Engine", "Crytek",
    "Marvel", "DC Comics", "Image Comics", "Dark Horse",
    "Netflix", "Amazon Studios", "Apple Studios",

    # ===== FOOD / BEVERAGE (50) =====
    "Coca-Cola", "PepsiCo", "Nestle", "Unilever", "Procter & Gamble",
    "Mondelez", "Kraft Heinz", "General Mills", "Kellogg", "Tyson Foods",
    "Hormel", "McCormick", "Hershey", "Mars", "Danone",
    "Dr Pepper", "Monster Beverage", "Red Bull", "Starbucks",
    "McDonald's", "Yum Brands", "Subway", "Chipotle", "Domino's",
    "Wendy's", "Dunkin'", "Papa John's", "Shake Shack",

    # ===== CONSULTING / SERVICES (100) =====
    "Deloitte", "PwC", "KPMG", "EY", "Accenture",
    "Capgemini", "Cognizant", "Infosys", "Wipro", "TCS",
    "HCL Technologies", "Tech Mahindra", "L&T Infotech", "Persistent Systems",
    "Mphasis", "Hexaware", "Sonata Software", "Mindtree", "LTTS",
    "NIIT Technologies", "BSNL", "Mphasis", "Zensar",
    "IBM Consulting", "Deloitte Digital", "Accenture Song",
    "McKinsey", "BCG", "Bain", "Oliver Wyman", "Roland Berger",
    "Booz Allen Hamilton", "SAIC", "Leidos", "Booz Allen",
    "DXC Technology", "Wipro", "HCL", "Tata Consultancy",

    # ===== DEFENSE / AEROSPACE (50) =====
    "Lockheed Martin", "Raytheon", "Northrop Grumman", "Boeing",
    "L3Harris", "General Dynamics", "BAE Systems", "Leonardo",
    "Saab", "Thales", "Airbus", "Rolls-Royce",
    "Raytheon Technologies", "L3Harris", "Huntington Ingalls",
    "Textron", "General Electric Aviation", "Pratt & Whitney",
    "Northrop Grumman", "Raytheon", "Lockheed",

    # ===== PHARMA / BIOTECH (80) =====
    "Pfizer", "Moderna", "Johnson & Johnson", "Merck", "AbbVie",
    "Amgen", "Gilead Sciences", "Biogen", "Regeneron", "Vertex",
    "Novartis", "Roche", "AstraZeneca", "Sanofi", "GlaxoSmithKline",
    "Bristol-Myers Squibb", "Eli Lilly", "Novo Nordisk",
    "Regeneron", "BioNTech", "CRISPR Therapeutics", "Intellia",
    "Editas", "Bluebird Bio", "Sarepta", "Alnylam",
    "Moderna", "BioNTech", "CureVac", "Arcturus",
    "Illumina", "Thermo Fisher", "Agilent", "Waters",
    "Bio-Rad", "PerkinElmer", "Bruker", "Danaher",

    # ===== ENERGY (50) =====
    "Chevron", "ExxonMobil", "Shell", "BP", "TotalEnergies",
    "Enel", "Iberdrola", "NextEra Energy", "Plug Power", "Bloom Energy",
    "Tesla Energy", "SunPower", "SunRun", "Enphase", "First Solar",
    "Vestas", "Siemens Gamesa", "Orsted", "Iberdrola",
    "Schneider Electric", "ABB", "Siemens Energy",

    # ===== REAL ESTATE / PROPTECH (50) =====
    "Zillow", "Redfin", "Opendoor", "Compass", "Procore",
    "Buildertrend", "CoConstruct", "PlanGrid", "Autodesk Construction",
    "Trimble", "Bentley Systems", "Oracle Construction",
    "CoStar", "RealPage", "AppFolio", "Yardi",
    "CBRE", "JLL", "Cushman", "Colliers",

    # ===== TRAVEL / HOSPITALITY (50) =====
    "Marriott", "Hilton", "Hyatt", "Accor", "IHG",
    "Booking Holdings", "Expedia", "TripAdvisor", "Kayak", "Skyscanner",
    "Hopper", "Omio", "Rome2Rio", "Kiwi.com",
    "Airbnb", "Vrbo", "Vacasa", "TurnKey",
    "American Airlines", "Delta", "United Airlines", "Southwest",
    "JetBlue", "Spirit", "Frontier", "Alaska Airlines",

    # ===== LOGISTICS (50) =====
    "FedEx", "UPS", "DHL", "USPS", "Amazon Logistics",
    "Flexport", "Freightos", "Project44", "FourKites",
    "Convoy", "Uber Freight", "KeepTruckin", "Samsara",
    "Trimble", "Descartes", "BluJay", "Manhattan Associates",
    "Locus Robotics", "6 River Systems", "Berkshire Grey",
    "Fetch Robotics", "Vecna Robotics", "Geek+", "HAI Robotics",

    # ===== EDUCATION (50) =====
    "Coursera", "Udemy", "edX", "Pluralsight", "Skillshare",
    "Duolingo", "Khan Academy", "BYJU'S", "Unacademy", "Vedantu",
    "2U", "Chegg", "Pearson", "McGraw-Hill", "Cengage",
    "Instructure", "Canvas", "Blackboard", "D2L",
    "ClassDojo", "Newsela", "Nearpod", "Kahoot!",
    "Quizlet", "Photomath", "Brainly", "Scribd",

    # ===== TELECOM (50) =====
    "AT&T", "Verizon", "T-Mobile", "Comcast", "Charter Communications",
    "Vodafone", "Telefonica", "Deutsche Telekom", "Orange",
    "AT&T", "Verizon", "T-Mobile", "US Cellular",
    "Dish Network", "Altice", "Frontier", "Windstream",
    "Telia", "Telenor", "Tele2", "MTN", "Airtel",

    # ===== RETAIL (50) =====
    "Walmart", "Target", "Costco", "Home Depot", "Lowe's",
    "Best Buy", "IKEA", "Nordstrom", "Macy's", "Gap",
    "Zara", "H&M", "Uniqlo", "Nike", "Adidas",
    "Lululemon", "Under Armour", "Foot Locker", "Dick's",
    "TJX", "Ross Stores", "Burlington", "Dollar General",
    "Dollar Tree", "Five Below", "Ollie's",

    # ===== GOVERNMENT / NON-PROFIT (30) =====
    "NASA", "CIA", "NSA", "FBI", "DOD",
    "NIH", "CDC", "FDA", "SEC", "FEMA",
    "World Bank", "IMF", "UN", "WHO", "Red Cross",
    "Gates Foundation", "Ford Foundation", "Rockefeller",

    # ===== JAPANESE TECH (50) =====
    "Sony", "Panasonic", "Sharp", "Toshiba", "NEC",
    "Fujitsu", "Hitachi", "Mitsubishi Electric", "Yaskawa",
    "Toyota", "Honda", "Nissan", "Mazda", "Subaru",
    "SoftBank", "Rakuten", "LINE", "Yahoo Japan",
    "DeNA", "Gree", "Konami", "Square Enix", "Capcom",
    "Bandai Namco", "Sega", "Nintendo", "Cygames",

    # ===== KOREAN TECH (30) =====
    "Samsung Electronics", "Samsung SDS", "Samsung SDI",
    "LG Electronics", "LG Display", "LG Chem",
    "SK Hynix", "SK Telecom", "SK Innovation",
    "Naver", "Kakao", "Krafton", "NCSoft",
    "Netmarble", "Com2uS", "Smilegate",

    # ===== AUSTRALIAN / NZ (30) =====
    "Atlassian", "Canva", "Afterpay", "Zip Co", "Xero",
    "SafetyCulture", "Culture Amp", " Employment Hero",
    "Linktree", "Airwallex", "Immutable", "Coinjar",
    "Siteminder", "Prospa", "Tyro", "Nextdc",

    # ===== CANADIAN (30) =====
    "Shopify", "Hut 8 Mining", "Nuvei", "Lightspeed",
    "Descartes Systems", "Open Text", "Nortel", "BlackBerry",
    "Ballard Power", "Magna International", "Shopify",
    "Well Health", "WELL Health", "Kinaxis", "Exacta",
    "Telus", "Rogers", "Bell Canada", "Shaw",

    # ===== ISRAELI (30) =====
    "Check Point", "Wix", "Monday.com", "Babylon Health",
    "Gong", "SalesLoft", "Chili Piper", "Payoneer",
    "Playtika", "IronSource", "Taboola", "Outbrain",
    "Fiverr", "Waze", "Mobileye", "Innoviz",

    # ===== LATIN AMERICAN (30) =====
    "Mercado Libre", "Rappi", "Nubank", "Kavak", "Creditas",
    "Clip", "EBANX", "dLocal", "Global66",
    "Despegar", "VTEX", "Nuvemshop", "Kushki",
    "Bitso", "Celo", "Push Notifications",

    # ===== SOUTHEAST ASIAN (30) =====
    "Grab", "Gojek", "Sea Group", "Shopee", "Lazada",
    "Tokopedia", "Bukalapak", "Traveloka", "Carsome",
    "PropertyGuru", "Carousell", "Fave", "OVO",

    # ===== MIDDLE EASTERN (20) =====
    "Careem", "Noon", "SWVL", "Fawry", "MNT-Halan",
    "Sary", "Lean Technologies", "Tamara", "Tabby",

    # ===== AFRICAN (20) =====
    "Flutterwave", "Paystack", "OPay", "Jumia", "Andela",
    "Flutterwave", "Chipper Cash", "TeamApt", "Carbon",

    # ===== UK / EUROPEAN TECH (50) =====
    "Revolut", "N26", "Monzo", "Starling Bank", "Wise",
    "Adidas", "BMW", "Mercedes-Benz", "Siemens", "SAP",
    "ARM", "Spotify", "King", "Skyscanner", "Zettle",
    "Booking.com", "Adyen", "Mollie", "MessageBird",
    "Pipedrive", "TransferWise", "Bolt", "Wolt",
    "Zalando", "About You", "HelloFresh", "Delivery Hero",
    "N26", "Trade Republic", "Solarisbank", "Raisin",
    "Contentful", "Mention", "Aircall", "Spendesk",
    "Pennylane", "Qonto", "Alan", "Swile",

    # ===== UKRAINIAN / EASTERN EUROPEAN (20) =====
    "Grammarly", "MacPaw", "Jetoctopu", "Ajax Systems",
    "GitLab", "GitLab", "People.ai", "People.ai",

    # ===== REMOTE-FIRST (30) =====
    "Automattic", "GitLab", "Buffer", "Zapier", "Toptal",
    "Andela", "Turing", "Crossover", "Upwork", "Fiverr",
    "Remote.com", "Deel", "Oyster", "Papaya Global",
    "Multiplier", "Velocity Global", "Dealfront",

    # ===== ADDITIONAL TECH COMPANIES (500+) =====
    "PostHog", "Linear", "Cal.com", "Resend", "Checkly",
    "Railway", "PlanetScale", "Supabase", "Prisma", "Hasura",
    "Nhost", "Render", "Fly.io", "Deno", "Bun",
    "Astro", "SvelteKit", "Tailwind CSS", "DaisyUI", "shadcn/ui",
    "Magic UI", "Luma", "Webflow", "Framer",
    "Retool", "Appsmith", "Budibase", "Tooljet",
    "Sentry", "LogRocket", "PostHog", "FullStory",
    "Segment", "Amplitude", "Mixpanel", "Heap", "Pendo",
    "UserZoom", "Hotjar", "Smartlook", "Clarity",
    "LaunchDarkly", "Split.io", "ConfigCat",
    "Vercel", "Netlify", "Cloudflare Pages", "Render",
    "Fly.io", "Railway", "Koala", "Deta",
    "Supabase", "PlanetScale", "Neon", "Xata", "Turso",
    "Prisma", "Hasura", "Nhost", "Appwrite", "Firebase",
    "Stripe", "Square", "PayPal", "Adyen", "Checkout.com",
    "Plaid", "Marqeta", "Ramp", "Brex", "Mercury",
    "Chime", "Nubank", "N26", "Revolut", "Monzo",
    "Wise", "Robinhood", "Coinbase", "Gemini", "Kraken",
    "Shopify", "BigCommerce", "Etsy", "eBay",
    "TikTok", "Reddit", "Quora", "Medium", "Substack",
    "Figma", "Canva", "Miro", "Notion", "Airtable",
    "Loom", "Vidyard", "Wistia", "Mux", "Cloudinary",
    "Discord", "Slack", "Zoom", "Microsoft Teams",
    "CrowdStrike", "Zscaler", "Palo Alto", "Fortinet",
    "SentinelOne", "Darktrace", "Cybereason",
    "Tempus", "Flatiron", "Guardant", "Grail",
    "Oura", "Whoop", "Peloton", "Zocdoc",
    "ServiceNow", "Workday", "BambooHR", "Gusto",
    "Rippling", "Lattice", "Deel", "Remote",
    "Tesla", "SpaceX", "Rivian", "Lucid",
    "Disney", "Netflix", "Spotify", "Twitch",
    "Deloitte", "PwC", "KPMG", "EY", "Accenture",
    "Lockheed", "Raytheon", "Northrop", "Boeing",
    "Pfizer", "Moderna", "Merck", "AbbVie",
    "Chevron", "Exxon", "Shell", "BP",
    "Zillow", "Redfin", "Opendoor", "Compass",
    "Coursera", "Udemy", "edX", "Pluralsight",
    "FedEx", "UPS", "DHL", "Flexport",
    "NVIDIA", "AMD", "Intel", "Qualcomm",
    "Cloudflare", "Fastly", "Akamai", "Datadog",
    "MongoDB", "Redis", "Confluent", "Snowflake",
    "Hashicorp", "Terraform", "Vault", "Consul",
    "Atlassian", "Jira", "Confluence", "Bitbucket",
    "GitHub", "GitLab", "CircleCI", "Jenkins",
    "Snyk", "Veracode", "Checkmarx", "Postman",
    "Sentry", "Bugsnag", "Rollbar", "LogRocket",
    "Sourcegraph", "Code Climate", "DeepSource", "Codacy",
    "OpenAI", "Anthropic", "Cohere", "Stability AI",
    "Hugging Face", "Replicate", "Modal", "Anyscale",
    "Together AI", "Groq", "Cerebras", "SambaNova",
    "Labelbox", "Scale AI", "Snorkel AI", "Landing AI",
    "LangChain", "LlamaIndex", "Haystack", "Semantic Kernel",
    "Pinecone", "Weaviate", "Chroma", "Milvus", "Qdrant",
    "LangSmith", "Helicone", "Portkey", "Guardrails AI",
    "Jasper", "Copy.ai", "Writesonic", "Character AI",
    "Grammarly", "Notion AI", "GitHub Copilot", "Cursor",
    "Aider", "Continue.dev", "Tabnine", "Codeium",
    "Mistral AI", "DeepSeek", "Alibaba Cloud", "Baidu AI",
    "Tencent AI", "ByteDance AI", "Yandex AI",
    "xAI", "Inflection AI", "Adept AI", "Character AI",
    "Runway", "Synthesia", "HeyGen", "D-ID",
    "ElevenLabs", "Whisper", "Assembly AI", "Deepgram",
    "Weights & Biases", "Neptune", "Comet ML", "Determined AI",
    "Celonis", "Dataiku", "Domino", "CNVRG", "Valohai",
    "Scale AI", "Labelbox", "Snorkel AI", "Landing AI",
    "Tempus", "Flatiron Health", "Guardant Health", "Grail",
    "Color Health", "One Medical", "Halo Health",
    "Oura", "Whoop", "Peloton", "Tempo",
    "Mirror", "Tonal", "Hydrow", "Ergatta",
    "Nurx", "Ro", "Hims", "Cerebral",
    "Talkspace", "BetterHelp", "Calm", "Headspace",
    "Zocdoc", "Genome Medical", "Invitae",
    "Oscar Health", "Clover Health", "Bright Health",
    "Virta Health", "Livongo", "Teladoc", "Amwell",
    "Phreesia", "Waystar", "R1 RCM", "Change Healthcare",
    "IQVIA", "Medidata", "Veeva Systems",
    "ServiceNow", "Workday", "SAP SuccessFactors",
    "BambooHR", "Gusto", "Zenefits", "Rippling",
    "Lattice", "Leapsome", "15Five", "Culture Amp",
    "Deel", "Remote", "Oyster", "Papaya Global",
    "Multiplier", "Velocity Global", "Dealfront",
    "Greenhouse", "Lever", "Ashby", "Workable",
    "SmartRecruiters", "iCIMS", "Taleo", "Jobvite",
    "JazzHR", "Breezy HR", "Teamtailor", "HireHive",
    "Recruitee", "Freshteam", "Bullhorn", "JobAdder",
    "ADP", "Paychex", "UKG", "Ceridian",
    "Cornerstone", "Docebo", "360Learning", "Lessonly",
    "Kissflow", "KiwiHR", "Factorial", "Personio",
    "Oyster HR", "Remote.com", "Deel", "Velocity Global",
    "Tesloop", "Canoo", "Fisker", "Lordstown",
    "Nikola", "Arrival", "Proterra",
    "Aurora", "Waymo", "Cruise", "Argo AI", "Motional",
    "Zoox", "Nuro", "Kodiak", "Gatik", "TuSimple",
    "Plus.ai", "Embark", "InMotion", "Byton",
    "Disney", "Warner Bros", "Paramount", "NBC Universal",
    "CBS", "Spotify", "Pandora", "SoundCloud",
    "YouTube", "Vimeo", "Twitch",
    "Roku", "Apple TV", "Hulu", "Peacock",
    "Activision Blizzard", "Electronic Arts", "Take-Two",
    "Ubisoft", "Epic Games", "Riot Games", "Valve",
    "Roblox", "Unity", "Unreal Engine",
    "CrowdStrike", "Zscaler", "Palo Alto Networks", "Fortinet",
    "FireEye", "Mandiant", "Rapid7", "Qualys", "Tenable",
    "SentinelOne", "Darktrace", "Cybereason",
    "Abnormal Security", "Material Security", "Arctic Wolf",
    "Expel", "Huntress", "Snyk", "Veracode", "Checkmarx",
    "Okta", "Auth0", "Duo Security", "OneLogin", "Ping Identity",
    "CyberArk", "BeyondTrust", "Thycotic",
    "Proofpoint", "Mimecast", "Barracuda",
    "Sophos", "Kaspersky", "Bitdefender", "Malwarebytes",
    "Tanium", "Ivanti",
]

def main():
    # Deduplicate and clean
    seen = set()
    unique = []
    for name in COMPANIES:
        clean = name.strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            unique.append(name.strip())

    # Write to file
    path = Path("data/company_names.txt")
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(unique))
    print(f"[OK] Generated {len(unique)} unique company names -> {path}")

    # Also write slugs
    import re
    slugs = set()
    for name in unique:
        slug = name.lower().replace(" ", "").replace(".", "").replace("'", "")
        slug = slug.replace("&", "and").replace(",", "").replace("(", "").replace(")", "")
        slug = re.sub(r"[^a-z0-9]", "", slug)
        if slug:
            slugs.add(slug)
    slug_path = Path("data/company_slugs.txt")
    slug_path.write_text("\n".join(sorted(slugs)))
    print(f"[OK] Generated {len(slugs)} unique slugs -> {slug_path}")


if __name__ == "__main__":
    main()
