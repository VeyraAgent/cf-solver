// cf-solver imperva runner: argv[2]=url, argv[3]=proxy(optional), argv[4]=userAgent(optional)
const path = require("path")
const IncapsulaSession = require(path.join(__dirname, "incapsula", "session.js"))
const DefaultUtmvcPayload = require(path.join(__dirname, "incapsula", "payloads", "utmvc.js"))
const DefaultReese84Payload = require(path.join(__dirname, "incapsula", "payloads", "reese84.js"))

const url = process.argv[2]
const proxyUrl = process.argv[3] || null
const userAgent = process.argv[4] || `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36`

;(async function(){
  try {
    const session = new IncapsulaSession(proxyUrl ? {proxyUrl, userAgent} : {userAgent})
    const result = await session.go({url, utmvc: DefaultUtmvcPayload, reese84: DefaultReese84Payload})
    console.log("__RESULT__" + JSON.stringify(result))
  } catch (e) {
    console.log("__RESULT__" + JSON.stringify({solved:false, error: String(e).slice(0,300)}))
  }
}())
