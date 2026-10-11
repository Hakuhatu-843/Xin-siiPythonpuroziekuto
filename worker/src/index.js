const ALLOWED_ORIGINS = new Set([
  "https://hakuhatu-843.github.io",
  "http://localhost:8000",
  "http://127.0.0.1:8000",
]);

function corsHeaders(origin) {
  const allowed = ALLOWED_ORIGINS.has(origin) ? origin : "https://hakuhatu-843.github.io";
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Admin-Key",
    "Content-Type": "application/json; charset=utf-8",
    "Vary": "Origin",
  };
}

function isAdmin(request, env) {
  const key = request.headers.get("X-Admin-Key");
  return Boolean(env.ADMIN_KEY && key && key === env.ADMIN_KEY);
}

function unauthorized(request) {
  return json({ ok: false, error: "Unauthorized" }, 401, request);
}

function json(data, status, request) {
  return new Response(JSON.stringify(data), {
    status,
    headers: corsHeaders(request.headers.get("Origin") || ""),
  });
}

function validatePayload(body) {
  if (!body || !["single", "bundle", "set"].includes(body.tradeType)) return "Invalid trade type";
  if (!Array.isArray(body.items) || body.items.length < 1 || body.items.length > 3) return "Items must contain 1 to 3 entries";
  if (body.tradeType === "single" && body.items.length !== 1) return "Single trade must contain one item";
  if (!Number.isInteger(body.totalPrice) || body.totalPrice < 1 || body.totalPrice > 2000000000) return "Invalid total price";
  if (typeof body.submissionId !== "string" || body.submissionId.length < 16 || body.submissionId.length > 100) return "Invalid submission ID";
  for (const item of body.items) {
    if (typeof item.characterNumber !== "string" || !/^\d{3}$/.test(item.characterNumber) ||
        typeof item.rarity !== "string" || item.rarity.length < 1 || item.rarity.length > 50 ||
        typeof item.mutation !== "string" || item.mutation.length < 1 || item.mutation.length > 50 ||
        !Number.isInteger(item.level) || item.level < 1 || item.level > 301 ||
        !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 9999) return "Invalid trade item";
  }
  return null;
}

function getClientIp(request) {
  return request.headers.get("CF-Connecting-IP") || null;
}

async function isBlocked(env, ip) {
  if (!ip) return false;
  const row = await env.DB.prepare("SELECT 1 FROM blocked_ips WHERE ip = ?").bind(ip).first();
  return Boolean(row);
}

async function isRateLimited(env, ip) {
  if (!ip) return false;
  const since = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  const row = await env.DB.prepare(
    "SELECT (SELECT COUNT(*) FROM trades WHERE client_ip = ? AND submitted_at >= ?) + (SELECT COUNT(*) FROM exchanges WHERE client_ip = ? AND submitted_at >= ?) AS count"
  ).bind(ip, since, ip, since).first();
  return Number(row?.count || 0) >= 10;
}

async function isInSubmissionCooldown(env, ip) {
  if (!ip) return false;
  const row = await env.DB.prepare(
    "SELECT MAX(submitted_at) AS last_at FROM (SELECT submitted_at FROM trades WHERE client_ip=? UNION ALL SELECT submitted_at FROM exchanges WHERE client_ip=?)"
  ).bind(ip, ip).first();
  if (!row?.last_at) return false;
  const elapsed = Date.now() - Date.parse(row.last_at);
  return Number.isFinite(elapsed) && elapsed < 5000;
}

async function submissionFingerprint(kind, body) {
  const canonical = {
    kind,
    tradeType: kind === "trade" ? body.tradeType : null,
    totalPrice: kind === "trade" ? body.totalPrice : null,
    exchangeDescription: kind === "exchange" ? body.exchangeDescription.trim() : null,
    items: body.items.map((item) => ({
      characterNumber: item.characterNumber,
      rarity: item.rarity,
      level: item.level,
      mutation: item.mutation,
      quantity: item.quantity
    }))
  };
  const bytes = new TextEncoder().encode(JSON.stringify(canonical));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function registerRepeatedSubmission(env, ip, fingerprint, now) {
  if (!ip) return false;
  const cutoff = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  await env.DB.prepare(
    "INSERT INTO repeated_submissions (ip, fingerprint, hit_count, first_seen, last_seen) VALUES (?, ?, 1, ?, ?) " +
    "ON CONFLICT(ip, fingerprint) DO UPDATE SET " +
    "hit_count = CASE WHEN repeated_submissions.last_seen >= ? THEN repeated_submissions.hit_count + 1 ELSE 1 END, " +
    "first_seen = CASE WHEN repeated_submissions.last_seen >= ? THEN repeated_submissions.first_seen ELSE excluded.first_seen END, " +
    "last_seen = excluded.last_seen"
  ).bind(ip, fingerprint, now, now, cutoff, cutoff).run();
  const row = await env.DB.prepare(
    "SELECT hit_count FROM repeated_submissions WHERE ip=? AND fingerprint=?"
  ).bind(ip, fingerprint).first();
  if (Number(row?.hit_count || 0) < 4) return false;
  await env.DB.prepare(
    "INSERT OR REPLACE INTO blocked_ips (ip, blocked_at) VALUES (?, ?)"
  ).bind(ip, now).run();
  return true;
}

async function validateCatalogItems(env, items) {
  for (const item of items) {
    const row = await env.DB.prepare("SELECT rarity, is_tradable FROM characters WHERE character_number = ?")
      .bind(item.characterNumber).first();
    if (!row || Number(row.is_tradable) !== 1 || row.rarity !== item.rarity) return "Character number not found";
  }
  return null;
}

async function getCharacters(request, env) {
  const result = await env.DB.prepare("SELECT character_number, english_name, japanese_name, rarity FROM characters WHERE is_tradable = 1 ORDER BY character_number").all();
  return json({ ok: true, characters: (result.results || []).map(row => ({
    characterNumber: row.character_number, characterId: row.character_number,
    characterName: row.japanese_name, englishName: row.english_name, rarity: row.rarity
  })) }, 200, request);
}

async function createTrade(request, env) {
  const ip = getClientIp(request);
  try {
    if (await isBlocked(env, ip)) return json({ ok: false, error: "Access blocked" }, 403, request);
    if (await isRateLimited(env, ip)) return json({ ok: false, error: "Too many submissions. Please try again later." }, 429, request);
  } catch (error) { console.error("trade abuse check failed", error); return json({ ok: false, error: "Database error" }, 500, request); }
  let body;
  try { body = await request.json(); } catch { return json({ ok: false, error: "Invalid JSON" }, 400, request); }
  const validationError = validatePayload(body);
  if (validationError) return json({ ok: false, error: validationError }, 400, request);
  try {
    const catalogError = await validateCatalogItems(env, body.items);
    if (catalogError) return json({ ok: false, error: catalogError }, 400, request);
    const existing = await env.DB.prepare("SELECT id, status, submitted_at FROM trades WHERE submission_id = ?").bind(body.submissionId).first();
    if (existing) return json({ ok: true, tradeId: existing.id, status: existing.status, submittedAt: existing.submitted_at, duplicate: true }, 200, request);
    const submittedAt = new Date().toISOString();
    const fingerprint = await submissionFingerprint("trade", body);
    if (await registerRepeatedSubmission(env, ip, fingerprint, submittedAt)) {
      return json({ ok: false, error: "Repeated identical submissions blocked this IP." }, 403, request);
    }
    if (await isInSubmissionCooldown(env, ip)) {
      return json({ ok: false, error: "Please wait 5 seconds before submitting again." }, 429, request);
    }
    const statements = [
      env.DB.prepare("INSERT INTO trades (submission_id, trade_type, total_amount, status, submitted_at, client_ip) VALUES (?, ?, ?, 'pending', ?, ?)")
        .bind(body.submissionId, body.tradeType, body.totalPrice, submittedAt, ip),
      ...body.items.map((item, i) => env.DB.prepare("INSERT INTO trade_characters (trade_id, position, character_id, rarity, level, mutation, quantity, is_verified) SELECT id, ?, ?, ?, ?, ?, ?, 0 FROM trades WHERE submission_id = ?")
        .bind(i + 1, item.characterNumber, item.rarity, item.level, item.mutation, item.quantity, body.submissionId))
    ];
    const result = await env.DB.batch(statements);
    const tradeId = result?.[0]?.meta?.last_row_id;
    if (!tradeId) throw new Error("D1 did not return a trade id");
    return json({ ok: true, tradeId, status: "pending", submittedAt }, 201, request);
  } catch (error) {
    if (String(error?.message || "").toLowerCase().includes("unique")) {
      const existing = await env.DB.prepare("SELECT id, status, submitted_at FROM trades WHERE submission_id = ?").bind(body.submissionId).first();
      if (existing) return json({ ok: true, tradeId: existing.id, status: existing.status, submittedAt: existing.submitted_at, duplicate: true }, 200, request);
    }
    console.error("createTrade failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

function pageParams(url) {
  const limit = Number(url.searchParams.get("limit") || "50");
  const after = Number(url.searchParams.get("after") || "0");
  const throughRaw = url.searchParams.get("through");
  if (!Number.isInteger(limit) || limit < 1 || !Number.isSafeInteger(after) || after < 0 ||
      (throughRaw !== null && (!Number.isSafeInteger(Number(throughRaw)) || Number(throughRaw) < 0))) return null;
  return { limit: Math.min(limit, 100), after, through: throughRaw === null ? null : Number(throughRaw) };
}

async function getTrades(request, env) {
  if (!isAdmin(request, env)) return unauthorized(request);
  const url = new URL(request.url), status = url.searchParams.get("status") || "pending", page = pageParams(url);
  if (!page || !["pending", "approved", "rejected"].includes(status)) return json({ ok: false, error: "Invalid pagination or status" }, 400, request);
  try {
    const maxRow = await env.DB.prepare("SELECT COALESCE(MAX(id), 0) AS id FROM trades").first();
    const through = page.through === null ? Number(maxRow?.id || 0) : page.through;
    const result = await env.DB.prepare(
      "WITH page AS (SELECT id, trade_type, total_amount, status, submitted_at, reviewed_by, reviewed_at, client_ip FROM trades WHERE status = ? AND id > ? AND id <= ? ORDER BY id ASC LIMIT ?) " +
      "SELECT p.id,p.trade_type,p.total_amount,p.status,p.submitted_at,p.reviewed_by,p.reviewed_at,p.client_ip,tc.position,tc.character_id,tc.rarity,tc.level,tc.mutation,tc.quantity,tc.is_verified,c.japanese_name " +
      "FROM page p LEFT JOIN trade_characters tc ON tc.trade_id=p.id LEFT JOIN characters c ON c.character_number=tc.character_id ORDER BY p.id ASC,tc.position ASC"
    ).bind(status, page.after, through, page.limit + 1).all();
    const map = new Map();
    for (const row of result.results || []) {
      if (!map.has(row.id)) map.set(row.id, { id: row.id, tradeType: row.trade_type, totalPrice: row.total_amount, status: row.status, submittedAt: row.submitted_at, reviewedBy: row.reviewed_by, reviewedAt: row.reviewed_at, clientIp: row.client_ip, items: [] });
      if (row.position !== null) map.get(row.id).items.push({ position: row.position, characterId: row.character_id, characterNumber: row.character_id, characterName: row.japanese_name || row.character_id, rarity: row.rarity, level: row.level, mutation: row.mutation, quantity: row.quantity, isVerified: row.is_verified });
    }
    const all = [...map.values()], hasMore = all.length > page.limit, trades = all.slice(0, page.limit);
    return json({ ok: true, trades, throughCursor: through, hasMore, nextCursor: hasMore ? trades[trades.length - 1].id : null }, 200, request);
  } catch (error) { console.error("getTrades failed", error); return json({ ok: false, error: "Database error" }, 500, request); }
}

async function createExchange(request, env) {
  const ip = getClientIp(request);
  try {
    if (await isBlocked(env, ip)) return json({ ok: false, error: "Access blocked" }, 403, request);
    if (await isRateLimited(env, ip)) return json({ ok: false, error: "Too many submissions. Please try again later." }, 429, request);
  } catch (error) { console.error("exchange abuse check failed", error); return json({ ok: false, error: "Database error" }, 500, request); }
  let body;
  try { body = await request.json(); } catch { return json({ ok: false, error: "Invalid JSON" }, 400, request); }
  if (!body || typeof body.submissionId !== "string" || body.submissionId.length < 16 || body.submissionId.length > 100 ||
      typeof body.exchangeDescription !== "string" || body.exchangeDescription.trim().length < 1 || body.exchangeDescription.trim().length > 1000 ||
      !Array.isArray(body.items) || body.items.length < 1 || body.items.length > 3) {
    return json({ ok: false, error: "Invalid exchange description" }, 400, request);
  }
  for (const item of body.items) {
    if (typeof item.characterNumber !== "string" || !/^\d{3}$/.test(item.characterNumber) ||
        typeof item.rarity !== "string" || typeof item.mutation !== "string" || !item.mutation ||
        !Number.isInteger(item.level) || item.level < 1 || item.level > 301 ||
        !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 9999) {
      return json({ ok: false, error: "Invalid trade item" }, 400, request);
    }
  }
  try {
    const catalogError = await validateCatalogItems(env, body.items);
    if (catalogError) return json({ ok: false, error: catalogError }, 400, request);
    const existing = await env.DB.prepare("SELECT id,status,submitted_at FROM exchanges WHERE submission_id=?").bind(body.submissionId).first();
    if (existing) return json({ ok: true, exchangeId: existing.id, status: existing.status, submittedAt: existing.submitted_at, duplicate: true }, 200, request);
    const submittedAt = new Date().toISOString();
    const fingerprint = await submissionFingerprint("exchange", body);
    if (await registerRepeatedSubmission(env, ip, fingerprint, submittedAt)) {
      return json({ ok: false, error: "Repeated identical submissions blocked this IP." }, 403, request);
    }
    if (await isInSubmissionCooldown(env, ip)) {
      return json({ ok: false, error: "Please wait 5 seconds before submitting again." }, 429, request);
    }
    const statements = [
      env.DB.prepare("INSERT INTO exchanges (submission_id,exchange_description,status,submitted_at,client_ip) VALUES (?,?,'pending',?,?)")
        .bind(body.submissionId, body.exchangeDescription.trim(), submittedAt, ip),
      ...body.items.map((item, i) => env.DB.prepare("INSERT INTO exchange_characters (exchange_id,position,character_id,rarity,level,mutation,quantity,is_verified) SELECT id,?,?,?,?,?,?,0 FROM exchanges WHERE submission_id=?")
        .bind(i + 1, item.characterNumber, item.rarity, item.level, item.mutation, item.quantity, body.submissionId))
    ];
    const result = await env.DB.batch(statements), exchangeId = result?.[0]?.meta?.last_row_id;
    if (!exchangeId) throw new Error("D1 did not return an exchange id");
    return json({ ok: true, exchangeId, status: "pending", submittedAt }, 201, request);
  } catch (error) {
    if (String(error?.message || "").toLowerCase().includes("unique")) {
      const existing = await env.DB.prepare("SELECT id,status,submitted_at FROM exchanges WHERE submission_id=?").bind(body.submissionId).first();
      if (existing) return json({ ok: true, exchangeId: existing.id, status: existing.status, submittedAt: existing.submitted_at, duplicate: true }, 200, request);
    }
    console.error("createExchange failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function getExchanges(request, env) {
  if (!isAdmin(request, env)) return unauthorized(request);
  const url = new URL(request.url), status = url.searchParams.get("status") || "pending", page = pageParams(url);
  if (!page || !["pending", "approved", "rejected"].includes(status)) return json({ ok: false, error: "Invalid pagination or status" }, 400, request);
  try {
    const maxRow = await env.DB.prepare("SELECT COALESCE(MAX(id),0) AS id FROM exchanges").first();
    const through = page.through === null ? Number(maxRow?.id || 0) : page.through;
    const result = await env.DB.prepare(
      "WITH page AS (SELECT id,exchange_description,status,submitted_at,reviewed_by,reviewed_at,client_ip FROM exchanges WHERE status=? AND id>? AND id<=? ORDER BY id ASC LIMIT ?) " +
      "SELECT p.id,p.exchange_description,p.status,p.submitted_at,p.reviewed_by,p.reviewed_at,p.client_ip,ec.position,ec.character_id,ec.rarity,ec.level,ec.mutation,ec.quantity,ec.is_verified,c.japanese_name " +
      "FROM page p LEFT JOIN exchange_characters ec ON ec.exchange_id=p.id LEFT JOIN characters c ON c.character_number=ec.character_id ORDER BY p.id ASC,ec.position ASC"
    ).bind(status, page.after, through, page.limit + 1).all();
    const map = new Map();
    for (const row of result.results || []) {
      if (!map.has(row.id)) map.set(row.id, { id: row.id, exchangeDescription: row.exchange_description, status: row.status, submittedAt: row.submitted_at, reviewedBy: row.reviewed_by, reviewedAt: row.reviewed_at, clientIp: row.client_ip, items: [] });
      if (row.position !== null) map.get(row.id).items.push({ position: row.position, characterId: row.character_id, characterNumber: row.character_id, characterName: row.japanese_name || row.character_id, rarity: row.rarity, level: row.level, mutation: row.mutation, quantity: row.quantity, isVerified: row.is_verified });
    }
    const all = [...map.values()], hasMore = all.length > page.limit, exchanges = all.slice(0, page.limit);
    return json({ ok: true, exchanges, throughCursor: through, hasMore, nextCursor: hasMore ? exchanges[exchanges.length - 1].id : null }, 200, request);
  } catch (error) { console.error("getExchanges failed", error); return json({ ok: false, error: "Database error" }, 500, request); }
}

async function updateExchangeStatus(request, env, exchangeId) {
  if (!isAdmin(request, env)) return unauthorized(request);
  if (!/^\d+$/.test(exchangeId)) return json({ ok: false, error: "Invalid exchange id" }, 400, request);
  let body;
  try { body = await request.json(); } catch { return json({ ok: false, error: "Invalid JSON" }, 400, request); }
  if (!["pending","approved","rejected"].includes(body?.status) || typeof body?.reviewerId !== "string" ||
      !body.reviewerId.trim() || body.reviewerId.trim().length > 100) return json({ ok: false, error: "Invalid status or reviewer" }, 400, request);
  try {
    const now = new Date().toISOString();
    const result = body.status === "pending"
      ? await env.DB.prepare("UPDATE exchanges SET status='pending',reviewed_by=NULL,reviewed_at=NULL WHERE id=? AND status IN ('approved','rejected')")
          .bind(Number(exchangeId)).run()
      : await env.DB.prepare("UPDATE exchanges SET status=?,reviewed_by=?,reviewed_at=? WHERE id=? AND status='pending'")
          .bind(body.status, body.reviewerId.trim(), now, Number(exchangeId)).run();
    if (Number(result.meta?.changes || 0) === 0) {
      const row = await env.DB.prepare("SELECT status FROM exchanges WHERE id=?").bind(Number(exchangeId)).first();
      if (!row) return json({ ok: false, error: "Exchange not found" }, 404, request);
      return json({ ok: false, error: "Review already completed or transition not allowed" }, 409, request);
    }
    await env.DB.prepare("UPDATE exchange_characters SET is_verified=? WHERE exchange_id=?")
      .bind(body.status === "approved" ? 1 : 0, Number(exchangeId)).run();
    return json({ ok: true, exchangeId: Number(exchangeId), status: body.status }, 200, request);
  } catch (error) { console.error("updateExchangeStatus failed", error); return json({ ok: false, error: "Database error" }, 500, request); }
}

async function getAdminStatistics(request, env) {
  if (!isAdmin(request, env)) return unauthorized(request);
  try {
    const countStatuses = async (table) => {
      const rows = await env.DB.prepare("SELECT status,COUNT(*) AS count FROM " + table + " GROUP BY status").all();
      const result = { total: 0, pending: 0, approved: 0, rejected: 0 };
      for (const row of rows.results || []) { const n = Number(row.count || 0); result[row.status] = n; result.total += n; }
      return result;
    };
    const trades = await countStatuses("trades"), exchanges = await countStatuses("exchanges");
    const verified = await env.DB.prepare("SELECT COUNT(*) AS count FROM trade_characters tc JOIN trades t ON t.id=tc.trade_id WHERE t.status='approved' AND tc.is_verified=1").first();
    const chars = await env.DB.prepare(
      "SELECT tc.character_id AS characterId,COUNT(DISTINCT tc.trade_id) AS tradeCount,SUM(tc.quantity) AS itemCount,MAX(t.submitted_at) AS lastSubmittedAt " +
      "FROM trade_characters tc JOIN trades t ON t.id=tc.trade_id WHERE t.status='approved' AND tc.is_verified=1 GROUP BY tc.character_id ORDER BY tc.character_id"
    ).all();
    return json({ ok: true, trades, exchanges, verifiedItems: Number(verified?.count || 0), characterCount: (chars.results || []).length, characters: chars.results || [], generatedAt: new Date().toISOString() }, 200, request);
  } catch (error) { console.error("getAdminStatistics failed", error); return json({ ok: false, error: "Database error" }, 500, request); }
}


async function getBlockedIps(request, env) {
  if (!isAdmin(request, env)) return unauthorized(request);
  try {
    const result = await env.DB
      .prepare("SELECT ip, blocked_at FROM blocked_ips ORDER BY blocked_at DESC")
      .all();
    return json({ ok: true, blockedIps: result.results || [] }, 200, request);
  } catch (error) {
    console.error("getBlockedIps failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function updateBlockedIp(request, env) {
  if (!isAdmin(request, env)) return unauthorized(request);

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "Invalid JSON" }, 400, request);
  }

  const ip = typeof body?.ip === "string" ? body.ip.trim() : "";
  const action = body?.action;
  const validIPv4 = (value) => {
    const parts = value.split(".");
    return parts.length === 4 && parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) >= 0 && Number(part) <= 255);
  };
  const validIPv6 = (value) => value.includes(":") && /^[0-9a-fA-F:.]+$/.test(value) && value.length <= 45;
  if (!ip || ip.length > 45 || !(validIPv4(ip) || validIPv6(ip)) || !["block", "unblock"].includes(action)) {
    return json({ ok: false, error: "Invalid IP or action" }, 400, request);
  }

  try {
    if (action === "block") {
      await env.DB
        .prepare("INSERT OR REPLACE INTO blocked_ips (ip, blocked_at) VALUES (?, ?)")
        .bind(ip, new Date().toISOString())
        .run();
    } else {
      await env.DB.prepare("DELETE FROM blocked_ips WHERE ip = ?").bind(ip).run();
    }
    return json({ ok: true, ip, action }, 200, request);
  } catch (error) {
    console.error("updateBlockedIp failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function updateTradeStatus(request, env, tradeId) {
  if (!isAdmin(request, env)) return unauthorized(request);
  if (!/^\d+$/.test(tradeId)) return json({ ok: false, error: "Invalid trade id" }, 400, request);
  let body;
  try { body = await request.json(); } catch { return json({ ok: false, error: "Invalid JSON" }, 400, request); }
  if (!["pending","approved","rejected"].includes(body?.status) || typeof body?.reviewerId !== "string" ||
      !body.reviewerId.trim() || body.reviewerId.trim().length > 100) return json({ ok: false, error: "Invalid status or reviewer" }, 400, request);
  try {
    const now = new Date().toISOString();
    const result = body.status === "pending"
      ? await env.DB.prepare("UPDATE trades SET status='pending',reviewed_by=NULL,reviewed_at=NULL WHERE id=? AND status IN ('approved','rejected')")
          .bind(Number(tradeId)).run()
      : await env.DB.prepare("UPDATE trades SET status=?,reviewed_by=?,reviewed_at=? WHERE id=? AND status='pending'")
          .bind(body.status, body.reviewerId.trim(), now, Number(tradeId)).run();
    if (Number(result.meta?.changes || 0) === 0) {
      const row = await env.DB.prepare("SELECT status FROM trades WHERE id=?").bind(Number(tradeId)).first();
      if (!row) return json({ ok: false, error: "Trade not found" }, 404, request);
      return json({ ok: false, error: "Review already completed or transition not allowed" }, 409, request);
    }
    await env.DB.prepare("UPDATE trade_characters SET is_verified=? WHERE trade_id=?")
      .bind(body.status === "approved" ? 1 : 0, Number(tradeId)).run();
    return json({ ok: true, tradeId: Number(tradeId), status: body.status }, 200, request);
  } catch (error) { console.error("updateTradeStatus failed", error); return json({ ok: false, error: "Database error" }, 500, request); }
}

async function getMarketSnapshot(request, env) {
  const url = new URL(request.url);
  const characterNumber = url.searchParams.get("characterNumber")?.trim() || "";
  const characterName = url.searchParams.get("characterName")?.trim() || "";

  if (Boolean(characterNumber) === Boolean(characterName)) {
    return json({ ok: false, error: "Provide characterNumber or characterName" }, 400, request);
  }
  if (characterNumber && !/^\\d{3}$/.test(characterNumber)) {
    return json({ ok: false, error: "Invalid character number" }, 400, request);
  }

  try {
    const character = characterNumber
      ? await env.DB.prepare(
          "SELECT character_number, english_name, japanese_name FROM characters WHERE character_number = ? AND is_tradable = 1"
        ).bind(characterNumber).first()
      : await env.DB.prepare(
          "SELECT character_number, english_name, japanese_name FROM characters WHERE is_tradable = 1 AND (japanese_name = ? OR english_name = ?) LIMIT 1"
        ).bind(characterName, characterName).first();

    if (!character) {
      return json(
        { ok: false, error: characterNumber ? "Character number not found" : "Character not found" },
        404,
        request
      );
    }

    const snapshot = await env.DB.prepare(
      "SELECT id, level1_value, level_max_value, demand_score, sample_count, level1_sample_count, level_max_sample_count, created_at FROM market_snapshots WHERE character_id = ? ORDER BY created_at DESC, id DESC LIMIT 1"
    ).bind(character.character_number).first();

    if (!snapshot) {
      return json({ ok: false, error: "Market snapshot not found" }, 404, request);
    }

    const changes = await getMarketSnapshotComparison(
      env,
      character.character_number,
      snapshot.id
    );
    const mutationRows = await env.DB.prepare(
      "SELECT mutation, multiplier, sample_count FROM market_snapshot_mutations WHERE snapshot_id = ? ORDER BY mutation"
    ).bind(snapshot.id).all();

    return json({
      ok: true,
      characterNumber: character.character_number,
      characterId: character.character_number,
      characterName: character.japanese_name,
      englishName: character.english_name,
      level1Value: snapshot.level1_value,
      levelMaxValue: snapshot.level_max_value,
      demandScore: snapshot.demand_score,
      sampleCount: Number(snapshot.sample_count || 0),
      level1SampleCount: snapshot.level1_sample_count == null ? null : Number(snapshot.level1_sample_count),
      levelMaxSampleCount: snapshot.level_max_sample_count == null ? null : Number(snapshot.level_max_sample_count),
      createdAt: snapshot.created_at,
      changes,
      mutations: (mutationRows.results || []).map((row) => ({
        mutation: row.mutation,
        multiplier: row.multiplier,
        sampleCount: Number(row.sample_count || 0),
        direction: changes.mutationDirections[row.mutation] || "→"
      }))
    }, 200, request);
  } catch (error) {
    console.error("getMarketSnapshot failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function getMarketValue(request, env) {
  const url = new URL(request.url);
  const characterId = url.searchParams.get("characterId")?.trim() || "";
  const mutation = url.searchParams.get("mutation")?.trim() || "";
  const level = Number(url.searchParams.get("level"));

  if (!characterId || characterId.length > 100) {
    return json({ ok: false, error: "Invalid characterId" }, 400, request);
  }
  if (!mutation || mutation.length > 50) {
    return json({ ok: false, error: "Invalid mutation" }, 400, request);
  }
  if (!Number.isInteger(level) || level < 1 || level > 301) {
    return json({ ok: false, error: "Invalid level" }, 400, request);
  }

  try {
    const result = await env.DB
      .prepare(
        `SELECT
          tc.level,
          tc.quantity,
          t.total_amount
        FROM trades t
        INNER JOIN trade_characters tc ON tc.trade_id = t.id
        WHERE t.status = 'approved'
          AND t.trade_type = 'single'
          AND tc.character_id = ?
          AND tc.mutation = ?
          AND tc.level BETWEEN ? AND ?
        ORDER BY tc.level ASC`
      )
      .bind(characterId, mutation, Math.max(1, level - 20), Math.min(301, level + 20))
      .all();

    const prices = (result.results || [])
      .map((row) => Number(row.total_amount) / Number(row.quantity))
      .filter((price) => Number.isFinite(price) && price > 0)
      .sort((a, b) => a - b);

    if (prices.length < 3) {
      return json(
        {
          ok: true,
          status: "insufficient_data",
          characterId,
          mutation,
          level,
          sampleCount: prices.length,
          marketValue: null,
        },
        200,
        request
      );
    }

    const middle = Math.floor(prices.length / 2);
    const median =
      prices.length % 2 === 1
        ? prices[middle]
        : (prices[middle - 1] + prices[middle]) / 2;

    return json(
      {
        ok: true,
        status: "ok",
        characterId,
        mutation,
        level,
        levelRange: {
          min: Math.max(1, level - 20),
          max: Math.min(301, level + 20),
        },
        sampleCount: prices.length,
        marketValue: Math.round(median),
      },
      200,
      request
    );
  } catch (error) {
    console.error("getMarketValue failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function calculateMarketForCharacter(env, characterId, level) {
  const result = await env.DB.prepare(
    `SELECT tc.quantity, t.total_amount
     FROM trades t
     INNER JOIN trade_characters tc ON tc.trade_id = t.id
     WHERE t.status = 'approved'
       AND t.trade_type = 'single'
       AND tc.character_id = ?
       AND tc.level BETWEEN ? AND ?`
  ).bind(characterId, Math.max(1, level - 20), Math.min(301, level + 20)).all();

  const prices = (result.results || [])
    .map((row) => Number(row.total_amount) / Number(row.quantity))
    .filter((price) => Number.isFinite(price) && price > 0)
    .sort((a, b) => a - b);

  if (prices.length < 3) {
    return { value: null, sampleCount: prices.length };
  }

  const middle = Math.floor(prices.length / 2);
  const median = prices.length % 2 === 1
    ? prices[middle]
    : (prices[middle - 1] + prices[middle]) / 2;

  return { value: Math.round(median), sampleCount: prices.length };
}

async function createMarketSnapshot(env) {
  const chars = await env.DB.prepare(
    "SELECT DISTINCT character_id FROM trade_characters WHERE is_verified = 1"
  ).all();

  const createdAt = new Date().toISOString();
  const snapshots = [];

  for (const row of chars.results || []) {
    const characterId = row.character_id;
    const lv1 = await calculateMarketForCharacter(env, characterId, 1);
    const lvMax = await calculateMarketForCharacter(env, characterId, 301);

    const tradeCount = await env.DB.prepare(
      `SELECT COUNT(*) AS count
       FROM trade_characters tc
       INNER JOIN trades t ON t.id = tc.trade_id
       WHERE t.status = 'approved' AND tc.character_id = ?`
    ).bind(characterId).first();

    const totalCount = await env.DB.prepare(
      `SELECT COUNT(*) AS count
       FROM trade_characters tc
       INNER JOIN trades t ON t.id = tc.trade_id
       WHERE t.status = 'approved'`
    ).first();

    const since = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    const recentCount = await env.DB.prepare(
      `SELECT COUNT(*) AS count
       FROM trade_characters tc
       INNER JOIN trades t ON t.id = tc.trade_id
       WHERE t.status = 'approved'
         AND tc.character_id = ?
         AND t.submitted_at >= ?`
    ).bind(characterId, since).first();

    const count = Number(tradeCount?.count || 0);
    const total = Number(totalCount?.count || 0);
    const recent = Number(recentCount?.count || 0);
    const shareScore = total > 0 ? (count / total) * 5 : 0;
    const recentScore = Math.min(recent / 5, 5);
    const demandScore = Math.round(
      Math.max(0, Math.min(5, shareScore * 0.6 + recentScore * 0.4)) * 10
    ) / 10;

    const snapshot = await env.DB.prepare(
      `INSERT INTO market_snapshots
       (character_id, level1_value, level_max_value, demand_score, sample_count,
        level1_sample_count, level_max_sample_count, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
    ).bind(
      characterId,
      lv1.value,
      lvMax.value,
      demandScore,
      count,
      lv1.sampleCount,
      lvMax.sampleCount,
      createdAt
    ).run();

    const snapshotId = snapshot.meta?.last_row_id;

    if (snapshotId) {
      const mutationRows = await env.DB.prepare(
        `SELECT tc.mutation,
                COUNT(*) AS sample_count,
                AVG(CAST(t.total_amount AS REAL) / tc.quantity) AS avg_unit_price
         FROM trade_characters tc
         INNER JOIN trades t ON t.id = tc.trade_id
         WHERE t.status = 'approved'
           AND tc.character_id = ?
           AND tc.is_verified = 1
           AND t.trade_type = 'single'
         GROUP BY tc.mutation`
      ).bind(characterId).all();

      const normalRow = (mutationRows.results || []).find(
        (row) => row.mutation === 'normal' || row.mutation === 'ノーマル'
      );
      const normalPrice = Number(normalRow?.avg_unit_price || 0);

      for (const row of mutationRows.results || []) {
        const avgPrice = Number(row.avg_unit_price || 0);
        const multiplier = normalPrice > 0
          ? Math.round((avgPrice / normalPrice) * 100) / 100
          : null;

        await env.DB.prepare(
          `INSERT INTO market_snapshot_mutations
           (snapshot_id, mutation, multiplier, sample_count)
           VALUES (?, ?, ?, ?)`
        ).bind(
          snapshotId,
          row.mutation,
          multiplier,
          Number(row.sample_count || 0)
        ).run();
      }
    }

    snapshots.push({
      characterId,
      snapshotId,
      level1Value: lv1.value,
      levelMaxValue: lvMax.value,
      sampleCount: Number(tradeCount?.count || 0)
    });
  }

  return snapshots;
}

function getChangeDirection(current, previous) {
  if (current == null || previous == null) return "→";
  if (current > previous) return "↑";
  if (current < previous) return "↓";
  return "→";
}

async function getMarketSnapshotComparison(env, characterId, currentSnapshotId) {
  const current = await env.DB.prepare(
    `SELECT id, level1_value, level_max_value, demand_score
     FROM market_snapshots WHERE id = ?`
  ).bind(currentSnapshotId).first();

  const previous = await env.DB.prepare(
    `SELECT id, level1_value, level_max_value, demand_score
     FROM market_snapshots
     WHERE character_id = ? AND id < ?
     ORDER BY created_at DESC
     LIMIT 1`
  ).bind(characterId, currentSnapshotId).first();

  const mutationCurrent = await env.DB.prepare(
    `SELECT mutation, multiplier
     FROM market_snapshot_mutations
     WHERE snapshot_id = ?`
  ).bind(currentSnapshotId).all();

  let mutationDirections = {};
  if (previous) {
    const mutationPrevious = await env.DB.prepare(
      `SELECT mutation, multiplier
       FROM market_snapshot_mutations
       WHERE snapshot_id = ?`
    ).bind(previous.id).all();

    const previousMap = new Map(
      (mutationPrevious.results || []).map((row) => [row.mutation, row.multiplier])
    );

    for (const row of mutationCurrent.results || []) {
      mutationDirections[row.mutation] = getChangeDirection(
        row.multiplier,
        previousMap.get(row.mutation)
      );
    }
  } else {
    for (const row of mutationCurrent.results || []) {
      mutationDirections[row.mutation] = "→";
    }
  }

  return {
    level1Direction: getChangeDirection(current?.level1_value, previous?.level1_value),
    levelMaxDirection: getChangeDirection(current?.level_max_value, previous?.level_max_value),
    demandDirection: getChangeDirection(current?.demand_score, previous?.demand_score),
    mutationDirections,
    hasPrevious: Boolean(previous)
  };
}

async function getTrade(request, env, tradeId) {
  if (!/^\d+$/.test(tradeId)) {
    return json({ ok: false, error: "Invalid trade id" }, 400, request);
  }

  const trade = await env.DB
    .prepare(
      "SELECT id, trade_type, total_amount, status, submitted_at FROM trades WHERE id = ?"
    )
    .bind(Number(tradeId))
    .first();

  if (!trade) {
    return json({ ok: false, error: "Trade not found" }, 404, request);
  }

  const result = await env.DB
    .prepare(
      "SELECT position, character_id, rarity, level, mutation, quantity, is_verified FROM trade_characters WHERE trade_id = ? ORDER BY position"
    )
    .bind(Number(tradeId))
    .all();

  return json(
    {
      id: trade.id,
      tradeType: trade.trade_type,
      totalPrice: trade.total_amount,
      status: trade.status,
      submittedAt: trade.submitted_at,
      items: result.results || [],
    },
    200,
    request
  );
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(createMarketSnapshot(env));
  },

  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(request.headers.get("Origin") || ""),
      });
    }

    const url = new URL(request.url);

    try {
      if (request.method === "GET" && url.pathname === "/health") {
        return json({ status: "ok" }, 200, request);
      }

      if (request.method === "GET" && url.pathname === "/characters") {
        return getCharacters(request, env);
      }

      if (request.method === "POST" && url.pathname === "/trades") {
        return createTrade(request, env);
      }

      if (request.method === "POST" && url.pathname === "/exchanges") {
        return createExchange(request, env);
      }

      if (request.method === "GET" && url.pathname === "/exchanges") {
        return getExchanges(request, env);
      }

      const exchangeStatusMatch = url.pathname.match(/^\/exchanges\/(\d+)\/status$/);
      if (request.method === "POST" && exchangeStatusMatch) {
        return updateExchangeStatus(request, env, exchangeStatusMatch[1]);
      }

      if (request.method === "GET" && url.pathname === "/admin/statistics") {
        return getAdminStatistics(request, env);
      }

      const statusMatch = url.pathname.match(/^\/trades\/(\d+)\/status$/);
      if (request.method === "POST" && statusMatch) {
        return updateTradeStatus(request, env, statusMatch[1]);
      }

      if (request.method === "GET" && url.pathname === "/trades") {
        return getTrades(request, env);
      }

      if (request.method === "GET" && url.pathname === "/market-snapshot") {
        return getMarketSnapshot(request, env);
      }

      if (request.method === "GET" && url.pathname === "/market-value") {
        return getMarketValue(request, env);
      }

      if (request.method === "GET" && url.pathname === "/admin/blocked-ips") {
        return getBlockedIps(request, env);
      }

      if (request.method === "POST" && url.pathname === "/admin/blocked-ips") {
        return updateBlockedIp(request, env);
      }

      const match = url.pathname.match(/^\/trades\/(\d+)$/);
      if (request.method === "GET" && match) {
        return getTrade(request, env, match[1]);
      }

      return json({ ok: false, error: "Not found" }, 404, request);
    } catch (error) {
      console.error("request failed", error);
      return json({ ok: false, error: "Internal server error" }, 500, request);
    }
  },
};
