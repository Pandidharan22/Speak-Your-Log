// All user-facing text, in English and Tamil, plus the mapping from API error codes to friendly
// messages. Keeping text in one place makes it reviewable (and the Tamil easy to improve).

export type Lang = "en" | "ta";

export interface Messages {
  appName: string;
  tagline: string;
  langSwitch: string;
  connectTitle: string;
  connectHelp: string;
  connectLink: string;
  tokenLabel: string;
  tokenHint: string;
  connectButton: string;
  connecting: string;
  connectedAs: (last4: string) => string;
  disconnect: string;
  startTitle: string;
  startHelp: string;
  privacyNotice: string;
  startButton: string;
  starting: string;
  micNeeded: string;
  statusConnecting: string;
  statusAgentSpeaking: string;
  statusListening: string;
  statusWaiting: string;
  transcriptTitle: string;
  you: string;
  agent: string;
  emptyTranscript: string;
  readBackTitle: string;
  readBackIntro: string;
  publicWarning: string;
  sayYes: string;
  postedTitle: string;
  postedBody: string;
  viewLog: string;
  postedNoLink: string;
  unknownTitle: string;
  unknownBody: string;
  failedTitle: string;
  failedBody: string;
  cancelledTitle: string;
  cancelledBody: string;
  again: string;
  endInterview: string;
  waking: string;
  errors: Record<string, string>;
}

const en: Messages = {
  appName: "Speak Your Log",
  tagline: "Talk for two minutes. Get a log in your own words.",
  langSwitch: "தமிழ்",
  connectTitle: "Connect your Proof account",
  connectHelp:
    "Paste your Proof token once. It is stored encrypted on the server and this page never sees it again.",
  connectLink: "Where do I get my token?",
  tokenLabel: "Proof token",
  tokenHint: "Create it in Proof, under Settings, then MCP.",
  connectButton: "Connect",
  connecting: "Checking your token…",
  connectedAs: (last4) => `Proof connected (ends in ${last4})`,
  disconnect: "Disconnect",
  startTitle: "Ready when you are",
  startHelp:
    "I will ask what you tried today, what broke, and why you chose it. Speak in Tamil, English, or a mix.",
  privacyNotice:
    "Your voice is processed by Google Gemini on its free tier, which may use it to improve Google products. No audio is stored by this app.",
  startButton: "Start talking",
  starting: "Starting…",
  micNeeded: "Please allow the microphone so I can hear you.",
  statusConnecting: "Connecting to the interviewer…",
  statusAgentSpeaking: "The interviewer is speaking",
  statusListening: "Listening to you",
  statusWaiting: "Waiting for the interviewer to join…",
  transcriptTitle: "Conversation",
  you: "You",
  agent: "Interviewer",
  emptyTranscript: "Your words will appear here as you speak.",
  readBackTitle: "Your log, in your own words",
  readBackIntro: "This is exactly what will be posted:",
  publicWarning: "It will be public on your Proof profile.",
  sayYes: 'Say "yes, post it" to post it, or tell me what to change.',
  postedTitle: "Posted",
  postedBody: "Your log is on your Proof record.",
  viewLog: "View your log on Proof",
  postedNoLink: "Posted. Open your Proof profile to see it.",
  unknownTitle: "We can't confirm it was posted",
  unknownBody:
    "The connection dropped at a bad moment. Please check your Proof profile before trying again, so you don't post it twice.",
  failedTitle: "It wasn't posted",
  failedBody: "Something went wrong and nothing was posted. You can start again.",
  cancelledTitle: "Stopped",
  cancelledBody: "Nothing was posted.",
  again: "Start again",
  endInterview: "End",
  waking: "The service is waking up. This can take up to a minute.",
  errors: {
    network_error: "Can't reach the service. Check your connection and try again.",
    invalid_token_format: "That doesn't look like a Proof token. Copy the whole token and try again.",
    token_rejected: "Proof didn't accept that token. Check it, or create a new one in Proof.",
    token_cannot_post: "That token can't post logs. Create a new token in Proof.",
    proof_rate_limited: "Proof is busy right now. Please try again in a minute.",
    proof_unavailable: "Proof isn't responding. Please try again shortly.",
    too_many_attempts: "Too many attempts. Please wait a minute and try again.",
    token_not_connected: "Connect your Proof token first.",
    busy_try_shortly: "The interviewer is busy with other students. Please try again in a moment.",
    too_many_interviews: "You've started a few interviews already. Please wait a few minutes.",
    voice_service_unavailable: "The voice service isn't available right now. Please try again shortly.",
    too_many_new_sessions: "Too many visitors from your network. Please try again in a minute.",
    unknown: "Something went wrong. Please try again.",
  },
};

const ta: Messages = {
  appName: "Speak Your Log",
  tagline: "இரண்டு நிமிடம் பேசுங்கள். உங்கள் வார்த்தைகளிலேயே ஒரு பதிவு.",
  langSwitch: "English",
  connectTitle: "உங்கள் Proof கணக்கை இணையுங்கள்",
  connectHelp:
    "உங்கள் Proof token-ஐ ஒரு முறை ஒட்டுங்கள். அது சர்வரில் மறைகுறியாக்கப்பட்டு சேமிக்கப்படும்; இந்தப் பக்கம் அதை மீண்டும் பார்க்காது.",
  connectLink: "Token எங்கே கிடைக்கும்?",
  tokenLabel: "Proof token",
  tokenHint: "Proof-இல் Settings, பிறகு MCP-இல் உருவாக்குங்கள்.",
  connectButton: "இணை",
  connecting: "Token-ஐ சரிபார்க்கிறோம்…",
  connectedAs: (last4) => `Proof இணைந்தது (முடிவு ${last4})`,
  disconnect: "இணைப்பை நீக்கு",
  startTitle: "நீங்கள் தயாரானதும் தொடங்கலாம்",
  startHelp:
    "இன்று என்ன try பண்ணீங்க, என்ன சரியா வேலை செய்யல, ஏன் அதைத் தேர்ந்தெடுத்தீங்க என்று கேட்பேன். தமிழிலோ ஆங்கிலத்திலோ கலந்தோ பேசலாம்.",
  privacyNotice:
    "உங்கள் குரல் Google Gemini-யின் இலவச பதிப்பில் செயலாக்கப்படுகிறது; அதை Google தன் தயாரிப்புகளை மேம்படுத்தப் பயன்படுத்தலாம். இந்த செயலி ஆடியோவை சேமிப்பதில்லை.",
  startButton: "பேச ஆரம்பிக்கவும்",
  starting: "தொடங்குகிறது…",
  micNeeded: "நான் உங்களைக் கேட்க மைக்ரோஃபோனை அனுமதியுங்கள்.",
  statusConnecting: "நேர்காணலருடன் இணைகிறது…",
  statusAgentSpeaking: "நேர்காணலர் பேசுகிறார்",
  statusListening: "உங்களைக் கேட்கிறோம்",
  statusWaiting: "நேர்காணலர் சேரக் காத்திருக்கிறோம்…",
  transcriptTitle: "உரையாடல்",
  you: "நீங்கள்",
  agent: "நேர்காணலர்",
  emptyTranscript: "நீங்கள் பேசும்போது உங்கள் வார்த்தைகள் இங்கே தெரியும்.",
  readBackTitle: "உங்கள் பதிவு, உங்கள் வார்த்தைகளில்",
  readBackIntro: "இதுதான் அப்படியே பதிவிடப்படும்:",
  publicWarning: "இது உங்கள் Proof சுயவிவரத்தில் பொதுவில் தெரியும்.",
  sayYes: '"ஆமா, post பண்ணுங்க" என்று சொல்லுங்கள்; அல்லது என்ன மாற்ற வேண்டும் என்று சொல்லுங்கள்.',
  postedTitle: "பதிவிடப்பட்டது",
  postedBody: "உங்கள் பதிவு உங்கள் Proof கணக்கில் உள்ளது.",
  viewLog: "Proof-இல் உங்கள் பதிவைப் பாருங்கள்",
  postedNoLink: "பதிவிடப்பட்டது. உங்கள் Proof சுயவிவரத்தில் பாருங்கள்.",
  unknownTitle: "பதிவானதா என்று உறுதிப்படுத்த முடியவில்லை",
  unknownBody:
    "தவறான நேரத்தில் இணைப்பு துண்டிக்கப்பட்டது. இரண்டு முறை பதிவாகாமல் இருக்க, மீண்டும் முயற்சிக்கும் முன் உங்கள் Proof சுயவிவரத்தைப் பாருங்கள்.",
  failedTitle: "பதிவாகவில்லை",
  failedBody: "ஏதோ தவறு நடந்தது; எதுவும் பதிவாகவில்லை. மீண்டும் தொடங்கலாம்.",
  cancelledTitle: "நிறுத்தப்பட்டது",
  cancelledBody: "எதுவும் பதிவாகவில்லை.",
  again: "மீண்டும் தொடங்கு",
  endInterview: "முடி",
  waking: "சேவை தயாராகிறது. ஒரு நிமிடம் வரை ஆகலாம்.",
  errors: {
    network_error: "சேவையை அடைய முடியவில்லை. இணைப்பைச் சரிபார்த்து மீண்டும் முயலுங்கள்.",
    invalid_token_format: "இது Proof token போலத் தெரியவில்லை. முழு token-ஐயும் நகலெடுங்கள்.",
    token_rejected: "இந்த token-ஐ Proof ஏற்கவில்லை. சரிபாருங்கள், அல்லது புதியதை உருவாக்குங்கள்.",
    token_cannot_post: "இந்த token-ஆல் பதிவிட முடியாது. Proof-இல் புதிய token உருவாக்குங்கள்.",
    proof_rate_limited: "Proof இப்போது பிஸியாக உள்ளது. ஒரு நிமிடம் கழித்து முயலுங்கள்.",
    proof_unavailable: "Proof பதிலளிக்கவில்லை. சிறிது நேரம் கழித்து முயலுங்கள்.",
    too_many_attempts: "அதிக முயற்சிகள். ஒரு நிமிடம் காத்திருந்து முயலுங்கள்.",
    token_not_connected: "முதலில் உங்கள் Proof token-ஐ இணையுங்கள்.",
    busy_try_shortly: "நேர்காணலர் வேறு மாணவர்களுடன் பிஸியாக இருக்கிறார். சிறிது நேரம் கழித்து முயலுங்கள்.",
    too_many_interviews: "ஏற்கனவே சில நேர்காணல்களைத் தொடங்கிவிட்டீர்கள். சில நிமிடங்கள் காத்திருங்கள்.",
    voice_service_unavailable: "குரல் சேவை இப்போது கிடைக்கவில்லை. சிறிது நேரம் கழித்து முயலுங்கள்.",
    too_many_new_sessions: "உங்கள் நெட்வொர்க்கிலிருந்து அதிக வருகைகள். ஒரு நிமிடம் கழித்து முயலுங்கள்.",
    unknown: "ஏதோ தவறு நடந்தது. மீண்டும் முயலுங்கள்.",
  },
};

export const MESSAGES: Record<Lang, Messages> = { en, ta };

/** Friendly text for an API error code; unknown codes fall back to a generic message. */
export function errorText(m: Messages, code: string): string {
  return m.errors[code] ?? m.errors.unknown ?? "";
}
