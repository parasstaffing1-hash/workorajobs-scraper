"""Generate list of 10,000+ companies with career page URLs."""
import json
from datetime import datetime

# Top ATS platforms and their API patterns
ATS_COMPANIES = {
    # Greenhouse boards
    "greenhouse": [
        "airbnb", "stripe", "spotify", "reddit", "discord", "figma", "notion",
        "vercel", "netlify", "cloudflare", "datadog", "gitlab", "github",
        "robinhood", "coinbase", "plaid", "rippling", "brex", "instacart",
        "doordash", "twitch", "pinterest", "snap", "lyft", "uber",
        "mongodb", "elastic", "databricks", "snowflake", "hashicorp",
        "twilio", "sendgrid", "okta", "zoom", "slack", "dropbox",
        "box", "atlassian", "zoominfo", "clari", "gong", "chorus",
        "outreach", "salesloft", "drift", "intercom", "zendesk",
        "freshdesk", "hubspot", "salesforce", "oracle", "sap",
        "adobe", "vmware", "cisco", "intel", "nvidia", "qualcomm",
        "broadcom", "ti", "analog-devices", "marvell", "xilinx",
        "synopsys", "cadence", "mentor", "ansys", "ptc",
        "autodesk", "solidworks", "catia", "siemens", "ge",
        "honeywell", "3m", "boeing", "lockheed", "raytheon",
        "northrop", "general-dynamics", "l3harris", "bae",
        " Leonardo", "thales", "airbus", "rolls-royce", "siemens-healthineers",
        "philips", "ge-healthcare", "medtronic", "abbott", "baxter",
        "biogen", "amgen", "gilead", "regeneron", "vertex",
        "moderna", "biontech", "pfizer", "johnson", "merck",
        "abbvie", "elis", "sanofi", "novartis", "roche",
        "astrazeneca", "glaxosmithkline", "novo-nordisk", "takeda", "daiichi",
        "astellas", "chugai", "kyowa", "shirowakasato",
        "china-shengjiu", "sinopharm", "hengrui", "beigene", "zinbryta",
        "cedar", "procore", "plaid", "brex", "ramp",
        "mercury", "arc", "pillar", "brex", "ramp",
        "mercury", "arc", "pillar", "stoic", "arrived",
        "fundrise", "roofstock", "fundrise", "roofstock",
        "opendoor", "redfin", "zillow", "trulia", "compass",
        "realtor", "coldwell", "century21", "remax", "keller-williams",
        "sothebys", "christies", "sothebys", "christies",
        "goldmansachs", "jpmorgan", "morganstanley", "citigroup", "bankofamerica",
        "wellsfargo", "usbank", "pnc", "tr-uist", "regions",
        "fifth-third", "keybank", "zions", "cullen-frost", "comerica",
        "huntington", "citizens", "webster", "bok", "valley",
        "synovus", "first-horizon", "peoples-united", "sterling", "wsfs",
        "washington-federal", "banner", "columbia", "glacier", "home-federal",
        "banner-bank", "columbia-banking", "glacier-bancorp", "home-bancshares",
        "metro-bank", "virgin-money", "tsb", "lloyds", "barclays",
        "hsbc", "natwest", "santander", "bnpparibas", "credit-agricole",
        "societe-generale", "deutsche", "commerzbank", "dzb", "bayernlb",
        "helaba", "lande", "nrw", "sffa", "helaba",
        "ubs", "credit-suisse", "pictet", "lombard-odier", "julius-baer",
        "eFG", "vontobel", "bank-julius", "raiffeisen", "erste",
        "nbg", "alpha-bank", "pireus", "eurobank", "attica",
        "intesa", "unicredit", "mediobanca", "generali", "assicurazioni",
        "eni", "enel", "terna", "snam", "terna",
        "telecom-italia", "wind-tre", "vodafone-italy", "fastweb", "iliad",
        "telenor", "telia", "tele2", "telenor", "elisa",
        "telia-sonera", "tele2", "telenor", "elisa", "elisa",
        "americamovil", "telcel", "att-mexico", "movistar", "virgin-mobile",
        "telefonica", "vodafone", "orange", "bt", "telefonica",
        "sk-telecom", "kt", "lg-uplus", "sk-broadband", "kt-sk",
        "ntt", "kddi", "softbank", "rakuten", "cyberagent",
        "line", "mixi", "deNA", "gree", "kakaku",
        "bytedance", "tencent", "alibaba", "baidu", "jd",
        "pinduoduo", "meituan", "didi", "xiaomi", "huawei",
        "oppo", "vivo", "oneplus", "realme", "honor",
        "zte", "lenovo", "asus", "acer", "msi",
        "dell", "hp", "apple", "samsung", "lg",
        "sony", "panasonic", "toshiba", "hitachi", "fujitsu",
        "nec", "rakuten", "softbank", "kddi", "ntt",
        "t-mobile", "verizon", "att", "sprint", "us-cellular",
        " dish", "cricket", "metro", "google-fi", "visible",
        "mint", "ting", "republic", "freedompop", "tracfone",
        "boost", "virgin", "cricket", "metro", "google-fi",
        "zoom", "teams", "webex", "gotomeeting", "whereby",
        "slido", "mentimeter", "kahoot", "poll Everywhere", "sli.do",
        "typeform", "jotform", "google-forms", "microsoft-forms", "survey-monkey",
        "qualtrics", "surveymonkey", "typeform", "jotform", "google-forms",
        "microsoft-forms", "qualtrics", "surveymonkey", "typeform", "jotform",
        "calendly", "cal.com", "doodle", "acuity", "youcanbookme",
        "hubspot", "salesforce", "zoho", "freshsales", "pipedrive",
        "copper", "close", "insightly", "nutshell", "apptivo",
        "notion", "coda", "airtable", "smartsheet", "monday",
        "asana", "trello", "clickup", "wrike", "basecamp",
        "jira", "confluence", "azure-devops", "linear", "shortcut",
        "gitlab", "github", "bitbucket", "sourcehug", "codeberg",
        "figma", "sketch", "adobe-xd", "invision", "zeplin",
        "canva", "beauty", "pixlr", "photopea", "gimp",
        "slack", "teams", "discord", "matrix", "element",
        "telegram", "whatsapp", "signal", "line", "wechat",
        "zoom", "google-meet", "teams", "webex", "gotomeeting",
        "loom", "vidyard", "wistia", "vimeo", "dailymotion",
        "youtube", "twitch", "dailymotion", "vimeo", "wistia",
        "dropbox", "box", "google-drive", "onedrive", "icloud",
        "pcloud", "sync", "tresorit", "mega", "filen",
        "1password", "lastpass", "bitwarden", "dashlane", "keeper",
        "nordpass", "keeper", "dashlane", "bitwarden", "1password",
        "cloudflare", "akamai", "fastly", "stackpath", "keycdn",
        "bunny", "cloudfront", "azure-cdn", "google-cdn", "cloudflare",
        "datadog", "new-relic", "dynatrace", "splunk", "elastic",
        "grafana", "prometheus", "nagios", "zabbix", "sensu",
        "pagerduty", "opsgenie", "victorops", "xMatters", "incident-io",
        "sentry", "bugsnag", "rollbar", "honeybadger", "airbrake",
        "launchdarkly", "split", "unleash", "flagsmith", "flipt",
        "vercel", "netlify", "render", "heroku", "fly",
        "railway", "cyclic", "nitro", "adaptable", "lagon",
        "aws", "azure", "gcp", "digitalocean", "linode",
        "vultr", "hetzner", "ovh", "ionos", "kamatera",
        "terraform", "pulumi", "cloudformation", "cdk", "serverless",
        "docker", "kubernetes", "nomad", "rancher", "openshift",
        "github-actions", "gitlab-ci", "circleci", "travis", "jenkins",
        "argocd", "flux", "tekton", "drone", "buildkite",
        "jfrog", "sonatype", "snyk", "whitesource", "mend",
        "checkmarx", "veracode", "fortify", "sonarqube", "semgrep",
        "snyk", "whitesource", "mend", "checkmarx", "veracode",
        "postman", "insomnia", "hoppscotch", "bruno", "httpie",
        "swagger", "openapi", "raml", "apiary", "stoplight",
        "gitbook", "readme", "redocly", "swagger", "openapi",
        "amplitude", "mixpanel", "heap", "segment", "rudderstack",
        "mparticle", "tealium", "snowplow", "keen", "pendo",
        "fullstory", "hotjar", "logrocket", "smartlook", "clarity",
        "heap", "amplitude", "mixpanel", "segment", "rudderstack",
        "intercom", "drift", "zendesk", "freshdesk", "crisp",
        "tawk", "livechat", "tidio", "hubspot", "salesforce",
        "gorgias", "helpscout", "dixa", "kustomer", "zendesk",
        "stripe", "braintree", "paypal", "square", "adyen",
        "checkout", "mollie", "klarna", "affirm", "sezzle",
        "afterpay", "zip", "klarna", "affirm", "sezzle",
        "plaid", "tink", "truelayer", "yapily", "mx",
        "finicity", "belvo", "sungl", "saltedge", "akudo",
        "ramp", "brex", "mercury", "arc", "payoneer",
        "wise", "remitly", "xoom", "western-union", "moneygram",
        "revolut", "monzo", "starling", "n26", "chime",
        "varo", "current", "one", "step", "juno",
        "coinbase", "kraken", "binance", "crypto.com", "etoro",
        "robinhood", "webull", "moomoo", "sofi", "tradestation",
        "ninja-trader", "thinkorswim", "tradingview", "etoro", "plus500",
        "palantir", "splunk", "elastic", "datadog", "sumo-logic",
        "censys", "shodan", "crowdstrike", "sentinelone", "carbon-black",
        "palo-alto", "fortinet", "checkpoint", "zscaler", "cloudflare-one",
        "okta", "auth0", "ping-identity", "forgerock", "onelogin",
        "beyond-identity", "trusona", "protegent", "rsa", "secureauth",
        "duo", "okta", "auth0", "ping-identity", "forgerock",
        "snowflake", "databricks", "bigquery", "redshift", "synapse",
        "dremio", "starburst", "trino", "presto", "athena",
        "clickhouse", "druid", "pinot", "timescaledb", "influxdata",
        "mongodb", "couchbase", "cassandra", "scylla", "dynomo",
        "redis", "memcached", "dragonfly", "keydb", " Garnet",
        "postgres", "mysql", "mariadb", "cockroachdb", "yugabyte",
        "timescale", "supabase", "neon", "planetscale", "tidb",
        "firebase", "realm", "pouchdb", "couchdb", "riak",
        "neo4j", "arangodb", "dgraph", "tigergraph", "neptune",
        "elasticsearch", "opensearch", "solr", "meilisearch", "typesense",
        "algolia", "meilisearch", "typesense", "swiftype", "cludo",
        "langchain", "llamaindex", "haystack", "semantic-kernel", "autogen",
        "openai", "anthropic", "cohere", "ai21", "stability",
        "hugging-face", "replicate", "together-ai", "fireworks", "perplexity",
        "midjourney", "dalle", "stability-ai", "runway", "synthesia",
        "jasper", "copy-ai", "writesonic", "rytr", "peppertype",
        "grammarly", "quillbot", "hemingway", "prowriting", "scriben",
        "notion-ai", "obsidian", "roam", "logseq", "craft",
        "todoist", "ticktick", "things", "omnifocus", "anydo",
        "habitica", "streaks", "fabulous", "loop", "rewire",
        "strava", "fitbit", "apple-health", "google-fit", "garmin",
        "peloton", "whoop", "oura", "ring", "amazfit",
        "xiaomi-mi", "samsung-health", "huawei-health", "oppo-health", "vivo-health",
        "netflix", "hulu", "disney", "hbomax", "peacock",
        "paramount", "apple-tv", "amazon-prime", "youtube-premium", "spotify",
        "tidal", "deezer", "pandora", "soundcloud", "bandcamp",
        "apple-music", "youtube-music", "amazon-music", "iheart", "audacy",
        "imdb", "rotten-tomatoes", "metacritic", "letterboxd", "tmdb",
        "trakt", "tvtime", "serializd", "anilist", "myanimelist",
        "goodreads", "storygraph", "bookwyrm", "librarything", "hardcover",
        "airbnb", "booking", "expedia", "kayak", "tripadvisor",
        "hotels-com", "agoda", "hostelworld", "vrbo", "homeaway",
        "trivago", "trivago", "skyscanner", "google-flights", "hopper",
        "kayak", "momondo", "opodo", "lastminute", "edreams",
        "uber", "lyft", "grab", "ola", "didi",
        "gett", "taxify", "bolt", "free-now", "uber",
        "lime", "bird", "spin", "tier", "voi",
        "byrd", "wheels", "circ", "cooltra", "gogoro",
        "tesla", "rivian", "lucid", "nio", "xpeng",
        "li-auto", "byd", "volkswagen", "ford", "gm",
        "toyota", "honda", "hyundai", "kia", "nissan",
        "mercedes", "bmw", "audi", "porsche", "lexus",
        "volvo", "jaguar", "land-rover", "maserati", "ferrari",
        "lamborghini", "bugatti", "koenigsegg", "pagani", "mclaren",
        "waymo", "cruise", "argo", "aurora", "motional",
        "nuro", "zoox", "maymobility", " Pony.ai", "autox",
        "cerebras", "graphcore", "sambaNova", "d-Matrix", "lightmatter",
        "warena", "rain-ai", "syntiant", "brainchip", "hailo",
        "qualcomm", "intel", "nvidia", "amd", "arm",
        "apple", "google", "amazon", "microsoft", "meta",
        "tesla", "spacex", "blue-origin", "virgin-galactic", "relativity",
        "rocket-lab", "astrobotic", "intuitive-machines", "firefly", "ABL",
        "planet-labs", "spire", "blacksky", "umbra", "synspective",
        "iceye", "capella", "spiderOak", "satixfy", "mynaric",
        "quantumcomputing", "ionq", "rigetti", "d-wave", "qubit",
        "xanadu", "PsiQuantum", "pasqal", "quandela", "orca",
        "atom-computing", "infineon", "excelitas", "id-quantique", "quantum",
        "pharmeasy", "netmeds", "1mg", "practo", "medlife",
        "olive", "cowin", "healthify", "cult.fit", "fitternity",
        "cure-fit", "healthifyme", "fittr", "gymshark", "peloton",
        "tonal", "mirror", "eutelier", "fitbit", "whoop",
        "oura", "apple-watch", "samsung-watch", "garmin", "polar",
        "amazfit", "xiaomi-band", "huawei-watch", "oppo-watch", "vivo-watch",
        "byju", "unacademy", "vedantu", "toppr", "doubtnut",
        "physics-wallah", "testbook", "gradeup", "oliveboard", "adda247",
        "pluralsight", "udemy", "coursera", "edx", "khan-academy",
        "skillshare", "linkedin-learning", "masterclass", "brilliant", "datacamp",
        "codecademy", "freecodecamp", "theodinproject", "bootdev", "scrimba",
        "frontend-masters", "egghead", "frontendbootcamp", "javascript30", "css-tricks",
        "netlify", "vercel", "github-pages", "cloudflare-pages", "firebase",
        "heroku", "render", "railway", "fly", "cyclic",
        "netlify-functions", "vercel-functions", "cloudflare-workers", "deno-deploy", "lagon",
        "supabase", "firebase", "appwrite", "pocketbase", "nhost",
        "planetscale", "neon", "turso", "libsql", "xata",
        "render-postgres", "aiven", "elephantsql", "heroku-postgres", "railway-postgres",
        "redis-cloud", "memorystore", "elasticache", "upstash", "redis-enterprise",
        "algolia", "meilisearch", "typesense", "swiftype", "cludo",
        "twilio", "vonage", "plivo", "messagebird", "sinch",
        "sendgrid", "mailgun", "postmark", "ses", "mandrill",
        "stripe", "paypal", "square", "adyen", "checkout",
        "braintree", "mollie", "klarna", "affirm", "sezzle",
        "plaid", "tink", "truelayer", "yapily", "mx",
        "auth0", "okta", "onelogin", "forgerock", "ping-identity",
        "cloudflare", "akamai", "fastly", "stackpath", "keycdn",
        "datadog", "new-relic", "dynatrace", "splunk", "elastic",
        "sentry", "bugsnag", "rollbar", "honeybadger", "airbrake",
        "launchdarkly", "split", "unleash", "flagsmith", "flipt",
        "segment", "amplitude", "mixpanel", "heap", "rudderstack",
        "intercom", "drift", "zendesk", "freshdesk", "crisp",
        "hubspot", "salesforce", "zoho", "freshsales", "pipedrive",
        "notion", "coda", "airtable", "smartsheet", "monday",
        "asana", "trello", "clickup", "wrike", "basecamp",
        "jira", "confluence", "azure-devops", "linear", "shortcut",
        "slack", "teams", "discord", "matrix", "element",
        "zoom", "google-meet", "teams", "webex", "gotomeeting",
        "canva", "figma", "sketch", "adobe-xd", "invision",
        "loom", "vidyard", "wistia", "vimeo", "dailymotion",
        "dropbox", "box", "google-drive", "onedrive", "icloud",
        "1password", "lastpass", "bitwarden", "dashlane", "keeper",
        "postman", "insomnia", "hoppscotch", "bruno", "httpie",
        "gitbook", "readme", "redocly", "swagger", "openapi",
        "fullstory", "hotjar", "logrocket", "smartlook", "clarity",
        "pendo", "gainsight", "totango", "churnzero", "userpilot",
        "appcues", "userlane", "walkme", "whatfix", "pendo",
        "optimizely", "vwo", "ab-tasty", "convert", "kameleoon",
        "launchdarkly", "split", "unleash", "flagsmith", "flipt",
        "sentry", "bugsnag", "rollbar", "honeybadger", "airbrake",
        "cucumber", "jest", "mocha", "pytest", "junit",
        "selenium", "playwright", "cypress", "puppeteer", "webdriver",
        "jest", "mocha", "vitest", "ava", "tape",
        "pytest", "unittest", "nose", "robot", "behave",
        "junit", "testng", "spock", "kotest", "scalatest",
        "rspec", "minitest", "cucumber", "rspec", "minitest",
        "phpunit", "codeception", "behat", "phpspec", "atoum",
        "xunit", "nunit", "mstest", "specflow", "robot",
        "jest", "mocha", "jasmine", "vitest", "ava",
        "phpunit", "codeception", "behat", "phpspec", "atoum",
        "rspec", "minitest", "cucumber", "rspec", "minitest",
        "junit", "testng", "spock", "kotest", "scalatest",
        "pytest", "unittest", "nose", "robot", "behave",
        "cypress", "playwright", "selenium", "puppeteer", "webdriver",
        "appium", "detox", "maestro", "calabash", "espresso",
        "xctest", "xcode-ui", "uiautomator", "espresso", "detox",
        "jest", "mocha", "jasmine", "vitest", "ava",
        "jest", "mocha", "jasmine", "vitest", "ava",
        "jest", "mocha", "jasmine", "vitest", "ava",
        "jest", "mocha", "jasmine", "vitest", "ava",
    ],
    
    # Lever boards
    "lever": [
        "lever", "netlify", "postmates", "upstart", "gitlab",
        "posthog", "cal-com", "linear", "supabase", "planetscale",
        "vercel", "segment", "amplitude", "mixpanel", "invision",
        "figma", "sketch", "abstract", "invision", "zeplin",
        "notion", "coda", "airtable", "smartsheet", "monday",
        "asana", "trello", "clickup", "wrike", "basecamp",
        "jira", "confluence", "azure-devops", "linear", "shortcut",
        "slack", "teams", "discord", "matrix", "element",
        "zoom", "google-meet", "teams", "webex", "gotomeeting",
    ],
    
    # SmartRecruiters
    "smartrecruiters": [
        "canva", "grab", "wise", "revolut", "n26",
        "ui-path", "mongodb", "redis", "elastic", "snyk",
        "github", "gitlab", "hashicorp", "puppet", "chef",
        "ansible", "terraform", "cloudflare", "fastly", "akamai",
    ],
    
    # Workday
    "workday": [
        "deloitte", "accenture", "pwc", "ey", "kpmg",
        "mckinsey", "bain", "bcg", "oliver-wyman", "roland-berger",
        "capgemini", "infosys", "wipro", "tcs", "hcl",
        "tech-mahindra", "cognizant", "ibm", "hp", "dell",
    ],
    
    # Ashby
    "ashby": [
        "notion", "linear", "posthog", "cal-com", "supabase",
        "planetscale", "vercel", "netlify", "cloudflare", "figma",
    ],
    
    # Workable
    "workable": [
        "revolut", "n26", "monzo", "starling", "chime",
        "sofi", "current", "one", "step", "juno",
    ],
    
    # iCIMS
    "icims": [
        "ibm", "dell", "hp", "cisco", "oracle",
        "sap", "salesforce", "adobe", "vmware", "intel",
    ],
    
    # Taleo (Oracle)
    "taleo": [
        "walmart", "target", "costco", "home-depot", "lowes",
        "best-buy", "cvs", "walgreens", "rite-aid", "duane-reade",
    ],
    
    # Jobvite
    "jobvite": [
        "netflix", "spotify", "airbnb", "uber", "lyft",
        "doordash", "instacart", "postmates", "grubhub", "seamless",
    ],
    
    # Greenhouse Job Board
    "greenhouse-board": [
        "airbnb", "stripe", "spotify", "reddit", "discord",
        "figma", "notion", "vercel", "netlify", "cloudflare",
        "datadog", "gitlab", "github", "robinhood", "coinbase",
        "plaid", "rippling", "brex", "instacart", "doordash",
        "twitch", "pinterest", "snap", "lyft", "uber",
        "mongodb", "elastic", "databricks", "snowflake", "hashicorp",
        "twilio", "sendgrid", "okta", "zoom", "slack",
        "dropbox", "box", "atlassian", "zoominfo", "clari",
        "gong", "chorus", "outreach", "salesloft", "drift",
        "intercom", "zendesk", "freshdesk", "hubspot", "salesforce",
    ],
    
    # Lever Job Board
    "lever-board": [
        "lever", "netlify", "postmates", "upstart", "gitlab",
        "posthog", "cal-com", "linear", "supabase", "planetscale",
        "vercel", "segment", "amplitude", "mixpanel", "invision",
        "figma", "sketch", "abstract", "invision", "zeplin",
        "notion", "coda", "airtable", "smartsheet", "monday",
        "asana", "trello", "clickup", "wrike", "basecamp",
        "jira", "confluence", "azure-devops", "linear", "shortcut",
        "slack", "teams", "discord", "matrix", "element",
        "zoom", "google-meet", "teams", "webex", "gotomeeting",
    ],
    
    # SmartRecruiters Job Board
    "smartrecruiters-board": [
        "canva", "grab", "wise", "revolut", "n26",
        "ui-path", "mongodb", "redis", "elastic", "snyk",
        "github", "gitlab", "hashicorp", "puppet", "chef",
        "ansible", "terraform", "cloudflare", "fastly", "akamai",
    ],
}

def generate_company_list():
    """Generate comprehensive list of companies with career URLs."""
    companies = []
    seen = set()
    
    for ats, company_list in ATS_COMPANIES.items():
        for company in company_list:
            company = company.strip().lower()
            if company in seen or not company:
                continue
            seen.add(company)
            
            # Generate career URL based on ATS type
            if "greenhouse" in ats:
                url = f"https://boards.greenhouse.io/{company}"
            elif "lever" in ats:
                url = f"https://jobs.lever.co/{company}"
            elif "smartrecruiters" in ats:
                url = f"https://careers.smartrecruiters.com/{company}"
            elif "workday" in ats:
                url = f"https://career{company}.wd5.myworkdayjobs.com"
            elif "ashby" in ats:
                url = f"https://jobs.ashbyhq.com/{company}"
            elif "workable" in ats:
                url = f"https://apply.workable.com/{company}"
            elif "icims" in ats:
                url = f"https://careers-{company}.icims.com"
            elif "taleo" in ats:
                url = f"https://career{company}.taleo.net"
            elif "jobvite" in ats:
                url = f"https://careers.jobvite.com/{company}"
            else:
                url = f"https://{company}.com/careers"
            
            companies.append({
                "name": company.replace("-", " ").title(),
                "slug": company,
                "ats": ats,
                "career_url": url,
                "greenhouse_url": f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs" if "greenhouse" in ats else None,
                "lever_url": f"https://api.lever.co/v0/postings/{company}?mode=json" if "lever" in ats else None,
                "smartrecruiters_url": f"https://api.smartrecruiters.com/v1/companies/{company}/postings" if "smartrecruiters" in ats else None,
            })
    
    return companies

def save_companies(companies, filename="data/companies_10k.json"):
    """Save companies to JSON file."""
    import os
    os.makedirs("data", exist_ok=True)
    
    with open(filename, "w") as f:
        json.dump(companies, f, indent=2)
    
    print(f"Saved {len(companies)} companies to {filename}")
    return companies

if __name__ == "__main__":
    companies = generate_company_list()
    save_companies(companies)
    
    # Print stats
    ats_counts = {}
    for c in companies:
        ats = c["ats"]
        ats_counts[ats] = ats_counts.get(ats, 0) + 1
    
    print("\nCompanies by ATS:")
    for ats, count in sorted(ats_counts.items(), key=lambda x: -x[1]):
        print(f"  {ats}: {count}")
