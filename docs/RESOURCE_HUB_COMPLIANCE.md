# Resource Hub Compliance & Developer Policy Statement

## 1. Purpose and Scope
Recap Studio's **Resource Hub** is a desktop discovery, reference bookmarking, and local media organization tool. It allows users to search public metadata, organize video links, and manage local media assets for their personal recap projects.

---

## 2. YouTube API Services Compliance
The YouTube integration within Resource Hub strictly adheres to the [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies) and [YouTube Terms of Service](https://www.youtube.com/t/terms).

### Core Policies Implemented:
1. **Search and Metadata Retrieval Only**:
   - The YouTube Data API v3 (`search` and `videos` endpoints) is utilized exclusively to discover public video references and retrieve public metadata (titles, channel names, publication dates, thumbnail URLs, and durations).
   - The application **does not** use YouTube API Services to download, record, capture, or cache YouTube audiovisual content.
   - Finding or previewing a video in the Resource Hub does not grant copyright permissions or rights to reuse third-party content.

2. **No Automatic Video Downloader Integration**:
   - Search results and YouTube resource bookmarks are never wired to an automated video downloading pipeline.
   - The application maintains a strict architectural separation between **External Video References** and **Authorized Local Media Assets**.
   - An external YouTube reference cannot be processed by the local Recap Pipeline unless the user provides an authorized local video file that they own or have the explicit right to process.

3. **Attribution and Embedded Playback**:
   - Previews of YouTube content use the official YouTube Embedded Player (`https://www.youtube.com/embed/{id}`) or direct links to YouTube.
   - No advertisements, watermarks, branding, or controls provided by YouTube are obscured, altered, or blocked.

4. **Data Retention and Expiration**:
   - In accordance with YouTube API Services Developer Policies (specifically regarding data caching and retention), API metadata is treated as temporary cached data.
   - Resource Hub enforces a maximum 30-day retention window on API-fetched metadata, with a conservative default refresh/expiry of 7 days.
   - Cached API metadata is refreshed or purged upon expiration.
   - User-generated information (personal notes, custom tags, category labels) is preserved independently as user data.

5. **Security and Secret Management**:
   - The user's YouTube Data API key is stored securely in local configuration on the user's personal machine.
   - API keys are never exposed in client-facing browser storage, frontend JavaScript, or API response payloads (keys are masked when returned via settings).
   - No YouTube user login credentials, cookies, or account tokens are gathered or requested.

---

## 3. Bilibili and External Platform Policies
1. **Honest Capability Disclosure**:
   - Bilibili integration functions as a reference URL bookmarking tool unless official API partner credentials and approvals are configured by the user.
   - The application does not scrape Bilibili HTML pages, circumvent anti-bot protections, or extract unauthorized cookies.
2. **Third-Party Video Rights**:
   - Users are reminded that media references saved from Bilibili or direct URLs remain subject to the respective platform's Terms of Service and copyright owner rights.

---

## 4. Local Media and Recap Pipeline Boundaries
- The local Recap processing pipeline (Whisper STT, Gemini scripting, FFmpeg splicing, VoxCPM2 TTS) operates **only on locally available files** provided directly by the user.
- Users must certify that they own or hold necessary permissions/licenses for any video file imported into the Recap Pipeline.

---

## 5. Important Disclosures & Links
- By using the YouTube features in Recap Studio Resource Hub, users agree to be bound by the [YouTube Terms of Service](https://www.youtube.com/t/terms).
- Please review the [Google Privacy Policy](https://policies.google.com/privacy) for details on how Google processes data.
- *Notice*: This application is an independent open-source project and has not received direct endorsement, sponsorship, or legal certification from YouTube, Google, or Bilibili.
