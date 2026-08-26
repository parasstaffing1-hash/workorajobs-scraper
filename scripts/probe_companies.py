#!/usr/bin/env python3
"""Probe real company names on all ATS platforms. Each valid board = 100-2000 unique jobs.
30 threads, checkpoint, auto-resume."""
from __future__ import annotations
import json, sqlite3, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP = ROOT / ".freebuff" / "probe_cp.json"
LOG = ROOT / ".freebuff" / "probe.log"
DB_LOCK = Lock()
S = httpx.Client(timeout=8, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})

def log(m):
    l = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(l, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(l + "\n")

def load_cp():
    if CP.exists():
        try: return json.loads(CP.read_text())
        except: pass
    return {"done": [], "new": 0, "valid": 0}

def save_cp(d):
    CP.parent.mkdir(parents=True, exist_ok=True)
    CP.write_text(json.dumps(d))

def mk_slugs(names):
    slugs = set()
    for n in names:
        c = n.strip().lower().replace(" ", "").replace(".", "").replace("-", "").replace("_", "")
        if 2 <= len(c) <= 40:
            slugs.add(c)
    return sorted(slugs)

NAMES = [
    "stripe","airbnb","spotify","twitter","reddit","pinterest","snap","discord",
    "figma","canva","notion","linear","gitlab","github","cloudflare","fastly",
    "vercel","netlify","datadog","newrelic","pagerduty","sentry","grafana",
    "databricks","snowflake","confluent","mongodb","elastic","redis",
    "cockroachlabs","planetscale","neon","supabase","hashicorp","pulumi",
    "docker","redhat","suse","okta","zscaler","crowdstrike","paloaltonetworks",
    "sentinelone","snyk","veracode","abnormalsecurity","huntress","expel",
    "openai","anthropic","xai","stabilityai","togetherai","scaleai",
    "assemblyai","cohere","replicate","modal","runpod","wandb",
    "square","plaid","brex","ramp","chime","sofi","affirm","klarna",
    "revolut","monzo","n26","wise","mercury","marqeta","galileo","nubank",
    "coinbase","robinhood","shopify","ebay","etsy","wayfair","flexport",
    "shipbob","shippo","stord","peloton","strava","oura","whoop",
    "epicgames","riotgames","roblox","unity","supercell","duolingo","coursera",
    "gusto","bamboohr","lattice","cultureamp","leapsome","deel","oyster",
    "personio","remote","factorial","hubspot","salesloft","outreach","gong",
    "intercom","drift","zendesk","freshdesk","braze","iterable","customerio",
    "klaviyo","twilio","sendgrid","mailchimp","dialpad","aircall","ringcentral",
    "amplitude","mixpanel","segment","heap","fullstory","posthog","hotjar",
    "metabase","looker","tableau","dbt","fivetran","airbyte","prefect","dagster",
    "airflow","pytorch","tensorflow","huggingface","cal.com","calendly",
    "loom","miro","whimsical","clickup","asana","monday","wrike","smartsheet",
    "trello","slack","zoom","launchdarkly","split","optimizely","statsig",
    "circleci","buildkite","jfrog","sonatype","checkmarx","auth0","onelogin",
    "vault","consul","nginx","envoy","traefik","rabbitmq","kafka","nats",
    "etcd","zookeeper","nacos","istio","linkerd","cilium","argocd","flux",
    "tekton","jira","confluence","bitbucket","sketch","invision","zeplin",
    "framer","webflow","wix","squarespace","bigcommerce","shopify",
    "salesforce","servicenow","workday","adobe","atlassian","sap","oracle",
    "vmware","cisco","juniper","arista","nvidia","amd","qualcomm","broadcom",
    "intel","arm","marvell","micron","texasinstruments","synopsys","cadence",
    "twilio","vonage","ringcentral","aircall","dialpad","fireflies",
    "metabase","looker","tableau","powerbi","dbt","fivetran","airbyte",
    "prefect","dagster","airflow","jupyter","deepnote","observable",
    "pytorch","tensorflow","jax","keras","sklearn","huggingface","transformers",
    "langchain","llamaindex","chroma","pinecone","weaviate","milvus","qdrant",
    "openai","anthropic","cohere","ai21","inflection","stability","midjourney",
    "runway","cursor","replit","codeium","tabnine","copilot",
    "vercel","netlify","cloudflare","render","railway","flyio","deno","bun",
    "nextjs","nuxtjs","sveltekit","remix","astro","solid","qwik",
    "vite","webpack","esbuild","turbo","rspack","rollup",
    "tailwindcss","bootstrap","material","chakra","radix","shadcnui",
    "react","vue","angular","svelte","solid","preact",
    "reactnative","flutter","swiftui","jetpackcompose","kotlin","swift",
    "java","python","go","rust","typescript","csharp","dotnet","nodejs","ruby",
    "php","elixir","erlang","haskell","clojure","scala","lua","r","julia",
    "aws","azure","gcp","oci","ibmcloud","alibabacloud","tencentcloud",
    "kubernetes","docker","helm","terraform","pulumi","crossplane","cdk","cdktf",
    "githubactions","gitlabci","circleci","jenkins","buildkite","drone",
    "prometheus","grafana","datadog","newrelic","dynatrace","splunk",
    "elastic","graylog","logzio","papertrail","logdna",
    "vault","keyvault","keycloak","auth0","okta","onelogin","pingidentity",
    "cloudflare","fastly","akamai","limelight","keycdn",
    "nginx","envoy","traefik","haproxy","caddy","lighttpd",
    "redis","memcached","varnish","squid","ats",
    "rabbitmq","kafka","nats","zeromq","mqtt","amqp",
    "grpc","thrift","avro","protobuf","capnp","flatbuffers",
    "etcd","zookeeper","consul","eureka","nacos","serf",
    "istio","linkerd","cilium","calico","flannel","weave",
    "docker","podman","containerd","crio","buildah","skopeo",
    "helm","kustomize","jsonnet","cue","yq","jq",
    "argocd","flux","tekton","cloudbuild","codebuild","codepipeline",
    "github","gitlab","bitbucket","gitea","gogs",
    "jira","asana","linear","height","shortcut","taiga",
    "notion","confluence","coda","airtable","clickup","todoist",
    "slack","teams","discord","mattermost","rocketchat","element",
    "zoom","meet","webex","gotomeeting","whereby","jitsi",
    "figma","sketch","invision","zeplin","abstract","marvel",
    "miro","excalidraw","whimsical","lucidchart","drawio","creately",
    "cal.com","calendly","acuity","doodle","when2meet","rallly",
    "loom","vidyard","wistia","brightcove","vimeo","sproutvideo",
    "intercom","drift","crisp","tawk","zendesk","freshdesk",
    "helpscout","groove","kayako","jetbrains","vscode","intellij",
    "pycharm","webstorm","rider","goland","clion","datagrip","phpstorm",
    "mongodb","postgresql","mysql","mariadb","sqlite","cockroachdb",
    "tidb","yugabyte","vitess","planetscale","supabase","neon","turso",
    "neo4j","arangodb","dgraph","fauna","elasticsearch","opensearch",
    "meilisearch","typesense","algolia","grafana","kibana","metabase",
    "superset","tableau","powerbi","looker","mode","hex","deepnote",
    "dbt","fivetran","airbyte","stitch","rivery","prefect","dagster",
    "airflow","luigi","jupyter","colab","kaggle","sagemaker","vertex",
    "mlflow","neptune","clearml","dvc","pytorch","tensorflow","jax","keras",
    "sklearn","huggingface","transformers","langchain","llamaindex","chroma",
    "pinecone","weaviate","milvus","qdrant","pgvector","openai","anthropic",
    "cohere","ai21","inflection","stability","midjourney","runway","cursor",
    "replit","codeium","tabnine","copilot","vercel","netlify","cloudflare",
    "render","railway","flyio","deno","bun","nextjs","nuxtjs","sveltekit",
    "remix","astro","solid","qwik","vite","webpack","esbuild","turbo",
    "rspack","rollup","parcel","snowpack","react","vue","angular","svelte",
    "solid","preact","lit","stencil","alpine","htmx","hotwire","turbo",
    "tailwindcss","bootstrap","material","chakra","radix","shadcnui",
    "mantine","arco","reactnative","expo","capacitor","ionic","nativescript",
    "taro","flutter","dart","swiftui","jetpackcompose","kotlinmultiplatform",
    "java","python","go","rust","typescript","csharp","dotnet","nodejs","ruby",
    "php","elixir","erlang","haskell","clojure","scala","lua","r","julia",
    "matlab","swift","kotlin","dart","razorpay","phonepe","groww","zerodha",
    "upstox","cred","slice","meesho","swiggy","zomato","ola","rapido",
    "freshworks","zoho","hasura","postman","darwinbox","citiustech","paytm",
    "bigbasket","blinkit","instamart","dunzo","ixigo","redbus","oyo",
    "makemytrip","goibibo","cleartrip","trivago","kayak","skyscanner",
    "booking","expedia","tripadvisor","hilton","marriott","accor","ihg",
    "walmart","target","costco","homedepot","lowes","bestbuy","starbucks",
    "mcdonalds","subway","chipotle","dominos","pizzahut","wendys","pepsi",
    "cocacola","nestle","unilepg","pg","mars","hershey","generalmills",
    "kellogg","pfizer","johnson","merck","novartis","roche","abbvie",
    "amgen","gilead","moderna","biontech","astrazeneca","shell","bp",
    "exxon","chevron","total","saudiaramco","boeing","airbus","lockheed",
    "northrop","raytheon","tcs","infosys","wipro","hcltech","techmahindra",
    "ltimindtree","persistent","mphasis","hexaware","bytedance","tiktok",
    "alibaba","tencent","baidu","grab","gojek","traveloka","shopee","lazada",
    "tokopedia","flipkart","policybazaar","curefit","practo","pharmeasy",
    "porter","blackbuck","rivigo","bluesmart","lucid","rivian","fisker",
    "nio","xpeng","byd","tesla","rivian","lucid","fisker","nio","xpeng",
    "byd","toyota","honda","ford","gm","bmw","mercedes","volkswagen",
    "nvidia","amd","qualcomm","broadcom","marvell","micron","texasinstruments",
    "synopsys","cadence","arm","intel","ibm","cisco","vmware","sap",
    "oracle","salesforce","servicenow","workday","adobe","atlassian",
    "microsoft","google","amazon","apple","meta","netflix","spotify",
    "twitter","snap","uber","lyft","airbnb","tesla","nvidia","amd",
    "qualcomm","broadcom","marvell","micron","texasinstruments","synopsys",
    "cadence","arm","intel","ibm","cisco","vmware","sap","oracle",
    "salesforce","servicenow","workday","adobe","atlassian","dell","hp",
    "lenovo","samsung","lg","sony","panasonic","nec","hitachi","toshiba",
    "fujitsu","asus","msi","acer","huawei","xiaomi","oppo","vivo",
    "oneplus","realme","nothing","pixel","apple","microsoft","google",
    "amazon","meta","netflix","spotify","twitter","snap","uber","lyft",
    "airbnb","tesla","nvidia","amd","qualcomm","broadcom","marvell",
    "micron","texasinstruments","synopsys","cadence","arm","intel","ibm",
    "cisco","vmware","sap","oracle","salesforce","servicenow","workday",
    "adobe","atlassian","dell","hp","lenovo","samsung","lg","sony",
    "panasonic","nec","hitachi","toshiba","fujitsu","asus","msi","acer",
    "huawei","xiaomi","oppo","vivo","oneplus","realme","nothing",
    "razorpay","phonepe","groww","zerodha","upstox","cred","slice",
    "meesho","swiggy","zomato","ola","rapido","freshworks","zoho",
    "hasura","postman","darwinbox","citiustech","paytm","bigbasket",
    "blinkit","instamart","dunzo","ixigo","redbus","oyo","makemytrip",
    "goibibo","cleartrip","trivago","kayak","skyscanner","booking",
    "expedia","tripadvisor","hilton","marriott","accor","ihg","walmart",
    "target","costco","homedepot","lowes","bestbuy","starbucks","mcdonalds",
    "subway","chipotle","dominos","pizzahut","wendys","pepsi","cocacola",
    "nestle","unilever","pg","mars","hershey","generalmills","kellogg",
    "pfizer","johnson","merck","novartis","roche","abbvie","amgen","gilead",
    "moderna","biontech","astrazeneca","shell","bp","exxon","chevron",
    "total","saudiaramco","boeing","airbus","lockheed","northrop","raytheon",
    "tcs","infosys","wipro","hcltech","techmahindra","ltimindtree",
    "persistent","mphasis","hexaware","bytedance","tiktok","alibaba",
    "tencent","baidu","grab","gojek","traveloka","shopee","lazada",
    "tokopedia","flipkart","policybazaar","curefit","practo","pharmeasy",
    "porter","blackbuck","rivigo","bluesmart","lucid","rivian","fisker",
    "nio","xpeng","byd","exponent","heretic","amp","anrok","archera",
    "baseten","braze","brightflag","bright Machines","buildkite",
    "canvas","caption","cast","chronicled","cirrus","coda","cognitive",
    "colossal","compas","conductor","construct","copilot","crux",
    "cymph","deploy","describe","digioh","dopefully","dose",
    "drata","dualoop","echo","elastic","element","embrace",
    "encord","engine","ensign","envoy","ethos","evabot","fathom",
    "finch","firebolt","fivetran","flowmo","flutterflow","flyte",
    "formsort","framer","freightpop","gamaya","gem","genability",
    "glean","glide","gondola","gorilla","greptile","groundcover",
    "growflow","gruuu","gudrun","gusto","hammock","handraise",
    "harmon","healtheintent","heretic","hightouch","hightop",
    "hivemind","hubii","human","hygraph","imburse","influx",
    "inngest","integrity","intruder","inventium","io.net","isomorphic",
    "iterable","jarvis","jetstack","jixie","jobbatical","journify",
    "kainos","kashable","kayo","kayzen","keap","keeper","kenchi",
    "keragon","keyfactor","kinetic","kishop","kitsu","klear",
    "klevu","knowatoa","kojo","kontent","koru","koyfin","kraaas",
    "krea","kuitive","kystack","labelbox","ladder","lakera",
    "lang","lateral","layer","leanix","legion","lemon","levels",
    "lightyear","lilac","limble","linen","listopro","litellm",
    "loadsmart","loop","loqo","lottiefiles","loverboard","luice",
    "lumApps","luma","luminary","lunchclub","lyra","machinelabs",
    "mage","mainframe","manifold","mapbox","marq","maven",
    "medusa","mellow","metomic","mighty","migrate","mindsdb",
    "mintlify","mitrais","monarch","monday","morrow","motorway",
    "mutable","mux","mytutor","nabla","nana","narmi","native",
    "natterbox","naukri","neeno","nemlig","neon","neptune",
    "netlify","network","neue","newfront","newrelic","nexus",
    "nimble","ninjarmm","noda","nomad","notion","novisto","nuxt",
    "nxtlevel","nxt","ob","obsidian","octane","octopus","officient",
    "ogury","omnicell","omnisend","omni","onetrust","onfleet",
    "open","openai","opendesk","openphone","openpath","openspace",
    "opswat","optimizely","option","orbit","os","osmium",
    "osteo","otter","outpost","owl","oyster","packagecloud",
    "pageproofer","pallet","pantheon","papaya","papercup",
    "parabola","parafin","parallel","parasol","pathable","patientpop",
    "patlytics","paybase","payfit","payflow","peanut","pears",
    "pebble","peek","peel","pelican","pendo","pensionbee",
    "percona","persona","perspective","petal","pfr","phantom",
    "phonepe","pilot","pilon","pipelane","pipelinedrive","pix",
    "planable","planetscale","planhat","planning","plantlogo",
    "platform","platter","plivo","plnar","plum","ply","pocketworks",
    "pockethotspot","point","pointgap","polco","polly","polpo",
    "pop","poparazzi","popchew","popsugar","portable",
    "portside","poshmark","postscript","postscript","potluck",
    "power","practical","prequel","prestodb","prettybird",
    "pricing","primate","primo","primrose","prince","principle",
    "printful","prisma","proceed","prod","prodeo","prodo",
    "productboard","productive","profixr","progressly","project",
    "project44","promethean","promethium","propel","prophesee",
    "prophet","prose","protocol","protoxa","proven","provence",
    "providence","proxidoc","prscribe","ps","pulumi","punchcard",
    "puppet","pure","purestorage","purple","pushpay","putnam","pyze",
    "qonto","quantexa","quantum","quartile","quc","quibi","quick",
    "quickbase","quickbridge","quill","quin","quinto","quirk",
    "quizlet","quotient","quote","r2c","rabbit","rad",
    "radar","radiant","rain","rainforest","rally","ramp","rappi",
    "rarible","ratelimiter","raygun","reach","rebate","recurly",
    "reddit","redfin","redhat","redox","redspot","reedsy",
    "reforge","reframe","reggora","relay","remix","remmit",
    "remotasks","remote","remotely","remotely","ren," "rentify",
    "repair","replay","repl","replit","reportgarden","repro",
    "reprise","rerun","respect","rest","restrain","retail",
    "retool","retrain","retro","reus","rev","rever","reversal",
    "revolut","reward","riff","ribbon","ring","ripple","riser",
    "ritual","rivian","rmg","roadpass","robinhood","robot",
    "robocorp","rocket","rocketlane","rocketreach","rockset",
    "roku","rollbar","root","roots","rosetta","round",
    "routable","rover","rubrik","ruggable","rush","rust",
    "sabre","saama","safeguard","safegraph","sailpoint","sailthru",
    "saks","salt","salto","sample","samsara","sanofi",
    "sap","sapphire","sauce","scalet","scalpy","scalyr",
    "scanner","scenic","schwab","scienaptic","scopely",
    "scout","screen","screenful","scrum","sd","seamless",
    "seattle","secur","secureauth","security","segal","segment",
    "selligent","semgrep","sendoso","sennder","sensata","sentry",
    "sequoia","serv","servicenow","sesame","ses","session",
    "sethi","sezzle","shades","shakes","shaper","sharetribe",
    "shelter","shippo","shipt","shockwave","shutterstock",
    "sift","signal","sign","signify","signifyd","silk",
    "sillo","silver","simple","simplilearn","simpli.fi","simply",
    "sinch","singlestore","sisense","siteimprove","siteminder",
    "sitter","skechers","skiff","skillshare","sky","skyscanner",
    "slack","slalom","snyk","social","soda","socrata","soft",
    "solar","solarisbank","soleco","solugen","sonatype",
    "song","songtrust","sonic","sophos","sorare","sourcetree",
    "span","spark","sparkcentral","sparx","spectrocloud",
    "speedscale","spendesk","splunk","spoke","spoki","spreedly",
    "sprinklr","spring","sprinklr","sprout","spryker","sqsp",
    "squid","stackadapt","stackshare","stadn","stan","stanley",
    "starling","stark","stateful","station","statista","statuspage",
    "stealth","steedos","stella","stellar","stencil","stitch",
    "stockx","stoplight","storm","storyblok","strava","stream",
    "street","stripe","strong","strongarm","stubhub","stumbleupon",
    "submittable","sugar","sumo","sunrun","super","superhuman",
    "supermetrics","supersede","supersolid","survey","surveygizmo",
    "susquehanna","sustainserv","swiggy","switch","synchrony",
    "syndio","taas","tableau","taboola","talend","talla",
    "talking","tamara","tangent","target","taro","tax",
    "taxact","taxslayer","teachable","teamwork","tebra","ted",
    "tegra","telaviv","telenav","telus","tempest","tenable",
    "tenor","teradata","terrameetch","terratec","tesla",
    "tesladaq","testlio","textio","thales","theaseanbanker",
    "theinfatuation","thill","think","threatmodeler","thrive",
    "tiaa","tidal","tiendeo","tilted","time","tinuiti",
    "tivo","tmobile","tokbox","tokopedia","tomtom","toolchain",
    "topia","topology","topox","torch","torchy","total",
    "touchbistro","toucan","toyota","trader","trado","trainual",
    "transfix","transload","transmit","treasure","tree",
    "trello","tremendous","tri","trifecta","trifacta","trigo",
    "tripadvisor","trov","true","truecaller","truist","trust",
    "truth","tsheets","turing","turn","turnitin","tusk",
    "twilio","twitch","twitter","typeform","uber","ugroop",
    "uitencent","ultimate","umbraco","unbabel","uncover",
    "underarmour","unified","unit","unity","univar","unomaly",
    "until","updater","upfront","uplight","upwork","urban",
    "urbanground","usabilla","usertesting","vanguard","varo",
    "vault","veem","veeqo","venmo","veracode","veritone",
    "verkada","versal","vibe","vimeo","vine","vinted","visa",
    "vital","vizio","vmware","vodafone","volta","vouch",
    "voxmedia","vsco","vtex","w3i","wah","walmart","wander",
    "warp","waze","wealthsimple","weave","webflow","weebly",
    "wework","wharton","whipsaw","whole","willy","windfall",
    "wip","wish","wistia","wix","wonder","wooha","woocommerce",
    "wordstream","wordpress","workable","workato","workiva",
    "workflow","workplace","workstream","wpp","xactly","xero",
    "xfinity","xola","xome","xoxoday","xseed","yale","yammer",
    "yapstone","yello","yellow","yesware","ymedia","yodlee",
    "youearnedit","yougov","young","youtube","yuno","zapier",
    "zendesk","zenefits","zenoss","zeplin","zerodha","zigbang",
    "zillow","zing","ziprecruiter","zocdoc","zoho","zola",
    "zomato","zoom","zops","zulip","zynga",
]

def gh(slug):
    try:
        r = S.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        if r.status_code != 200: return []
        d = r.json(); jobs = d.get("jobs", [])
        if not jobs: return []
        return [{"title": j.get("title",""), "company": d.get("name",slug),
                 "location": (j.get("location",{}) or {}).get("name","") if isinstance(j.get("location"),dict) else str(j.get("location","")),
                 "url": j.get("absolute_url",""), "posted_at": j.get("updated_at") or j.get("created_at"),
                 "jobkey": str(j.get("id","")), "source": f"greenhouse:{slug}",
                 "description": (j.get("content") or "")[:500],
                 "tags": (j.get("departments") or [{}])[0].get("name","") if j.get("departments") else ""} for j in jobs]
    except: return []

def lv(slug):
    try:
        r = S.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if r.status_code != 200: return []
        d = r.json()
        if not isinstance(d, list) or not d: return []
        return [{"title": j.get("text",""), "company": j.get("categories",{}).get("team",slug),
                 "location": j.get("categories",{}).get("location",""),
                 "url": j.get("hostedUrl",""),
                 "posted_at": datetime.fromtimestamp(j.get("createdAt",0)/1000, tz=timezone.utc).isoformat() if j.get("createdAt") else None,
                 "jobkey": j.get("id",""), "source": f"lever:{slug}",
                 "description": (j.get("descriptionPlain") or "")[:500],
                 "tags": j.get("teamsPlain","")} for j in d]
    except: return []

def ab(slug):
    try:
        r = S.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code != 200: return []
        d = r.json(); b = d.get("jobBoard",{}); ops = b.get("openings",[])
        if not ops: return []
        return [{"title": j.get("title",""), "company": b.get("name",slug),
                 "location": j.get("locationName",""), "url": j.get("url",""),
                 "posted_at": j.get("publishedAt"), "jobkey": j.get("id",""),
                 "source": f"ashby:{slug}", "description": "",
                 "tags": j.get("departmentName","")} for j in ops]
    except: return []

def sr(slug):
    try:
        r = S.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
        if r.status_code != 200: return []
        d = r.json(); c = d.get("content",[])
        if not c: return []
        return [{"title": j.get("name",""), "company": j.get("company",{}).get("name",slug),
                 "location": ((j.get("location") or {}).get("city","")+", "+((j.get("location") or {}).get("country",""))).strip(", "),
                 "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref','')}",
                 "posted_at": j.get("releasedDate"), "jobkey": str(j.get("id","")),
                 "source": f"smartrecruiters:{slug}", "description": "", "tags": ""} for j in c]
    except: return []

def probe(slug):
    for fn in [gh, lv, ab, sr]:
        jobs = fn(slug)
        if jobs: return jobs
    return []

def store(conn, jobs, tag):
    new = 0; now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
        for j in jobs:
            if not j.get("title") or not j.get("url"): continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (j["url"] or j.get("jobkey",""), j["title"], j.get("company",""),
                     j.get("location",""), j.get("description",""), j["url"],
                     j["source"], "ats", j.get("jobkey",""), j.get("posted_at"),
                     j.get("salary",""), tag, now, now))
                if cur.rowcount > 0: new += 1
            except: continue
        conn.commit()
    return new

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=30)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    slugs = mk_slugs(NAMES)
    log(f"Slugs to probe: {len(slugs)}")

    cp = load_cp() if args.resume else {"done": [], "new": 0, "valid": 0}
    done = set(cp["done"])
    remaining = sorted(set(slugs) - done)
    log(f"Done: {len(done)}, Remaining: {len(remaining)}")

    conn = sqlite3.connect(DB)
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {before:,}")

    gn, gv = cp["new"], cp["valid"]
    start = time.time()
    bs = args.threads * 10

    for bi in range(0, len(remaining), bs):
        batch = remaining[bi:bi+bs]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]; done.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        gv += 1
                        src = jobs[0].get("source","?")
                        new = store(conn, jobs, f"probe,{slug}")
                        gn += new
                        if new > 0:
                            log(f"  +{slug:30s} {src:30s} {len(jobs):4d} +{new:4d}")
                except: pass

        save_cp({"done": list(done), "new": gn, "valid": gv})
        el = time.time() - start
        cur = before + gn
        rate = gn / (el/60) if el > 0 else 0
        log(f"  Batch {bi//bs+1}: {cur:,} (+{gn:,}) | {gv} valid | {rate:.0f}/min")

    el = time.time() - start
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    log(f"\n{'='*60}")
    log(f"Slugs: {len(done)} | Valid: {gv} | New: {gn:,} | Total: {final:,}")
    log(f"Time: {el/60:.1f}min | Gap 1M: {max(0,1000000-final):,}")
    log(f"{'='*60}")

if __name__ == "__main__":
    main()
