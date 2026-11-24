# The clients

Voltrieve has one server and four clients. Each client shows one audience, so someone who watches a demo can tell what it is meant to show. The design is in [the car, the website and attachments](https://github.com/mklab-se/voltrieve/blob/main/docs/superpowers/specs/2026-11-23-car-website-attachments-design.md).

| Client | Audience | Shows | Where |
| --- | --- | --- | --- |
| The car | The driver | The assistant inside the car, with functions you see and hear: speed, wipers, horn, parking, and a photo of a road sign | `clients/car/` |
| The website | A customer who is signed in | Asking Runa about your own car, with a photo if you have one | `clients/web/` |
| The operations dashboard | Voltrieve employees | What happens in the service: users, response times, cost, errors | `clients/dashboard/` |
| The CLI | Developers | A test tool for the API. It does not stand in for a car | `clients/cli/` |

```mermaid
flowchart LR
    Driver(["Driver"]) --> Car["Car<br/>set_speed, set_wipers, honk,<br/>park, get_sign_photo"]
    Customer(["Customer"]) --> Web["Website: My Voltrieve<br/>set_theme, photo of a part"]
    Staff(["Employee"]) --> Dash["Operations dashboard<br/>no chat"]
    Dev(["Developer"]) --> CLI["CLI<br/>make_noise, honk, get_local_time,<br/>show_banner, set_gauge"]
    Car -->|"POST /attachments, /chat"| API
    Web -->|"POST /attachments, /chat"| API
    CLI -->|"/chat"| API
    Dash -->|"GET /admin/live"| API
    Entra["Entra ID<br/>one registration per client"] -. tokens .-> Car
    Entra -. tokens .-> Web
    Entra -. tokens .-> Dash
    Entra -. tokens .-> CLI
    API["Web API"] --> Backends["A prompt agent, B own loop, C hosted agent"]
```

## Status

All four clients are built in this repository: the car, the website, the operations dashboard and the CLI. This page was checked against the code of the car on 2026-11-24. The pictures on it were taken with mock servers, so the answers and the numbers in them are mock data.

Run live on 2026-11-24: the car, the website and the dashboard in a browser against the deployed API, with a token from the az CLI in place of the sign-in page ([`clients-live.txt`](https://github.com/mklab-se/voltrieve/blob/main/docs/evidence/clients-live.txt)). From scripts: photo turns with the car's and the website's context on all three backends ([Photos and attachments](attachments.md#measured)), and the car's five tools on all three backends, 18 of 18. The three sites are deployed and show their sign-in page (looked at by hand).

Not verified:

- sign-in to the car, the new website and the dashboard,
- the car in Safari or Firefox, on a slow or Windows computer, or with a screen reader,
- whether a person hears the horn and the motor.

## What the clients share

- **Sign-in.** Each browser client is a public client with its own app registration (`voltrieve-web`, `voltrieve-car`, `voltrieve-dashboard`) and the same delegated scope, `access_as_user`. The CLI has `voltrieve-cli`. Users need the `Chat.User` role. The dashboard needs `Dashboard.View`. See [Deployment](deploy.md).
- **One stream.** All chat clients read the same server-sent events and answer the same `client_tool_calls` through `/chat/tool-results`. The car and the website are copies of one another in their sign-in, stream parser, turn state and answer rendering, with a comment in each that says where its twin lives.
- **Tools run on the client.** A client declares the tools it offers in every request. The API never runs them. What a tool does is decided in the client.
- **Which backend answers.** The header `X-Agent-Backend` picks one of the three. For the prompt agent, the API picks the agent whose tools include every declared one: web, then CLI, then car. No match gives `unsupported_client_tool`.
- **The notice.** The car (in its menu) and the website (under the chat) say the same about the live screen and the stored chat, and then: "A nightly job counts what people ask about. It saves only topics shared by several people, never your chat." The job is not running yet. The car hides its menu below 1280 pixels, as it hides the car.

## The car

The car is the Spark, the city car in saffron yellow. The driver sees a picture of its dashboard from the driver's seat, with a street in 3D behind the windscreen and in the rear view mirror. A typed message stands in for the driver talking. Voice is not built.

![The car at 50 km/h on a made-up street, with the last parking sign and Park here? on the center screen](images/car-driving.webp)

*Driving at 50 km/h. The center screen shows the last sign that was passed. Mock data.*

| Tool | What happens |
| --- | --- |
| `set_speed(kmh)`, 0 to 150 | The car accelerates or brakes to it. From a bay it pulls out first, and finishes that also when it is told to stop |
| `set_wipers(mode)`: off, slow, fast | The wipers move. Slow and fast also start rain |
| `honk(times)`, 1 to 5 | A horn is heard. A browser blocks sound until the first click in the car, and then the tool says so |
| `park()` | The car pulls into the next bay it has room to stop for. The result says when the bay belongs to another block than the sign asked about |
| `get_sign_photo()` | Uploads the last photo of a sign and returns its attachment id with the result |

The driver can do some of it alone: the up and down keys, or the Go and Brake buttons, drive the car, and the speaker button turns the sound of the motor on. Typing anywhere starts a message. The driver display shows gear, speed, range, battery, wipers and the distance to the next sign. Its range and battery are made up.

The street is endless and made of blocks of 400 m. One parking rule holds for a block, and its sign stands at the start, followed by five bays. There are eight signs, and they come in the same order again and again. When the car passes a sign, a second camera takes a close-up of 1024 x 1024 pixels, where the sign fills most of the photo, so the smallest row of text is large enough to read. Above 70 km/h the photo is blurred, and the server rejects it as `too_blurry`. The photos were checked offline with the server's own photo handler: all eight signs were accepted at 50 km/h, and all eight rejected at 90.

"Park here?" sends the last photo with the question "Can I park here?". If the photo cannot be used, the question goes alone. Runa is then expected to ask for the photo with `get_sign_photo`, get the reason and tell the driver. The car keeps its last 10 photos and uploads again when the API has lost one.

![Runa's answer to Can I park here? floats above the dashboard while the car stands in a bay](images/car-parking.webp)

*Parked and asking. The answer is scripted in the mock server and is not a reading of the sign: the real agents read the photo.*

The answer to a question floats above the dashboard in large type, then sinks back into the center screen after 12 quiet seconds, or when the driver closes it or presses Escape. It stays while Runa works, and while the pointer or keyboard focus is in it. When the car has come to another sign, it says that the answer is for the previous block. The car needs a window of at least 1280 pixels wide, and a smaller one gets a message that says so. It was looked at from 1280 x 720 to 2560 x 1440. The clock on the screen can be set to any day and time, and it is sent as the local time in the context, with the car, the odometer and the speed.

| | |
| --- | --- |
| ![A long answer with three distances floats above the dashboard](images/car-answer.webp) | ![Rain on the windscreen, the wipers on fast](images/car-rain.webp) |
| *A long answer while it streams. Mock data.* | *Wipers on fast, and rain. Mock data.* |

The cockpit and five house fronts are generated pictures ([how they were made](https://github.com/mklab-se/voltrieve/blob/main/clients/car/assets/README.md)), and the product owner has not picked them yet. The rest of the street is drawn by code. Without Azure, `npm run mock` and `npm run dev:mock` in `clients/car` run the car against a mock server on port 8789.

**`set_speed` does what it is told.** It is a simulation. A real car must decide for itself what it accepts, whatever the model asks for. See [Decisions](decisions.md#set_speed-does-what-it-is-told).

**Under the hood.** A switch in the menu in the corner, off by default, opens the event timeline and the backend picker beside the car. When it is off, nothing on the screen mentions backends or events. The car starts on the own loop.

## The website

"My Voltrieve" is a customer's page: name, their car with picture, registration, trim, color, odometer and next service, and the chat as "Ask Runa about your car". The car data is made up and lives in the client. It is sent with each message as `context`, so Runa knows which car is meant. Suggested questions are about that car. The customer's Spark is in the Plus trim. A button attaches a photo, with a preview, a remove button and drag and drop.

The gauge, `set_gauge`, the name "Flight Recorder" and the dashboard are not in the website. The website declares only `set_theme`. "Under the hood" works as in the car: timeline, backend picker and correlation id, off by default.

![My Voltrieve: the customer's car on the left, and a chat where Runa answers about a photo](images/website-photo.webp)

*A photo on the website. The photo is a catalog picture and the answer is scripted in the mock server, so the two do not match. Mock data, and a test user.*

![The same page in the dark theme with Under the hood on: the backend picker and the event timeline](images/website-under-the-hood.webp)

*Under the hood. Mock data. The note under the chat is cut off in both pictures, since its text was corrected afterwards.*

## The operations dashboard

Its own app with its own sign-in. It shows the live feed and links to Live Metrics and the workbook, for users with the `Dashboard.View` role. See [the runbook](runbook.md#the-operations-dashboard).

![The live dashboard: users, messages, cost and first text per backend, and one card for each turn](images/dashboard.webp)

*The live dashboard with mock data: the times and costs are not measurements. The note says "never stored", which was corrected afterwards: the live feed stores nothing, but [Foundry's tracing stores text](decisions.md#open-the-product-owner-decides-what-foundrys-tracing-may-record).*

## The CLI

A command line for developers. It declares `make_noise`, `honk`, `get_local_time`, `show_banner` and, when asked, `set_gauge`, runs on every backend and shows the whole trace with `--trace`. Most of the recorded evidence comes from scripts that speak the same contract. It no longer stands in for a car: the car is the car client.
