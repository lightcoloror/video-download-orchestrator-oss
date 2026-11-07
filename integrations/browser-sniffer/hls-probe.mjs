export function variantUriAtIndex(playlistText, variantIndex) {
  const lines = String(playlistText || "").split(/\r?\n/);
  let index = -1;
  for (let position = 0; position < lines.length; position += 1) {
    if (!lines[position].trim().startsWith("#EXT-X-STREAM-INF:")) continue;
    index += 1;
    for (let candidate = position + 1; candidate < lines.length; candidate += 1) {
      const value = lines[candidate].trim();
      if (!value) continue;
      if (value.startsWith("#")) break;
      if (index === variantIndex) return value;
      break;
    }
  }
  return null;
}

export function resolveSafeVariantUrl(masterUrl, variantUri) {
  try {
    const master = new URL(masterUrl);
    const resolved = new URL(variantUri, master);
    if (!["http:", "https:"].includes(resolved.protocol)) return { status: "unsafe_protocol", url: null };
    if (resolved.username || resolved.password) return { status: "userinfo_not_allowed", url: null };
    if (resolved.origin !== master.origin) return { status: "cross_origin_waiting", url: null };
    if (isBlockedHostname(resolved.hostname)) return { status: "unsafe_host", url: null };
    if (isKnownMediaResource(resolved.pathname)) return { status: "media_resource_not_followed", url: null };
    return { status: "ok", url: resolved.toString() };
  } catch {
    return { status: "invalid_variant_uri", url: null };
  }
}

export function storageSafeUrl(value) {
  try {
    const parsed = new URL(value);
    parsed.username = "";
    parsed.password = "";
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return "";
  }
}

function isKnownMediaResource(pathname) {
  return /\.(?:ts|m4s|mp4|m4a|aac|mp3|webm|key)(?:$|\/)/i.test(pathname);
}

function isBlockedHostname(hostname) {
  const normalized = String(hostname || "").toLowerCase().replace(/^\[|\]$/g, "");
  if (normalized === "localhost" || normalized.endsWith(".localhost") || normalized === "metadata.google.internal") return true;
  if (normalized.includes(":")) return true;
  const parts = normalized.split(".").map((item) => Number.parseInt(item, 10));
  if (parts.length !== 4 || parts.some((item) => !Number.isInteger(item) || item < 0 || item > 255)) return false;
  return parts[0] === 10
    || parts[0] === 127
    || (parts[0] === 169 && parts[1] === 254)
    || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
    || (parts[0] === 192 && parts[1] === 168);
}