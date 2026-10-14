export function mediaType(url, contentType = "") {
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  if (!["http:", "https:"].includes(parsed.protocol)) return null;
  const mime = contentType.split(";")[0].trim().toLowerCase();
  if (["application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl"].includes(mime)) return "m3u8";
  if (mime === "video/mp4") return "mp4";
  if (/\.m3u8$/i.test(parsed.pathname)) return "m3u8";
  if (/\.mp4$/i.test(parsed.pathname)) return "mp4";
  return null;
}

export function mergeCapture(items, capture) {
  const previous = items.find(item => item.url === capture.url);
  const remaining = items.filter(item => item.url !== capture.url);
  return [...remaining, {...previous, ...capture}].slice(-100);
}
