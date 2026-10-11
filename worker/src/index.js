const ALLOWED_ORIGINS = new Set([
  "https://hakuhatu-843.github.io",
  "http://localhost:8000",
  "http://127.0.0.1:8000",
]);
const MAX_REQUEST_BODY_BYTES = 16 * 1024;

class RequestBodyTooLargeError extends Error {}

async function parseJsonBody(request) {
  const contentLength = request.headers.get("Content-Length");
  if (
    contentLength !== null &&
    /^\d+$/.test(contentLength) &&
    Number(contentLength) > MAX_REQUEST_BODY_BYTES
  ) {
    throw new RequestBodyTooLargeError();
  }

  const reader = request.body?.getReader();
  if (!reader) throw new SyntaxError("Request body is empty");
  const chunks = [];
  let totalBytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    totalBytes += value.byteLength;
    if (totalBytes > MAX_REQUEST_BODY_BYTES) {
      await reader.cancel();
      throw new RequestBodyTooLargeError();
    }
    chunks.push(value);
  }

  const body = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
}

function invalidJsonResponse(request, error) {
  return error instanceof RequestBodyTooLargeError
    ? json({ ok: false, error: "Request body too large" }, 413, request)
    : json({ ok: false, error: "Invalid JSON" }, 400, request);
}

function corsHeaders(origin) {
  const allowed = ALLOWED_ORIGINS.has(origin) ? origin : "https://hakuhatu-843.github.io";
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Admin-Key, X-Review-Key",
    "Content-Type": "application/json; charset=utf-8",
    "Vary": "Origin",
  };
}

function isAdmin(request, env) {
  const key = request.headers.get("X-Admin-Key");
  return Boolean(env.ADMIN_KEY && key && key === env.ADMIN_KEY);
}

function isReviewer(request, env) {
  const reviewKey = request.headers.get("X-Review-Key");
  return Boolean(env.REVIEW_KEY && reviewKey && reviewKey === env.REVIEW_KEY) ||
    isAdmin(request, env);
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
  if (!body || !["single", "bundle", "set"].includes(body.tradeType)) {
    return "Invalid trade type";
  }

  if (!Array.isArray(body.items) || body.items.length < 1 || body.items.length > 3) {
    return "Items must contain 1 to 3 entries";
  }

  if (body.tradeType === "single" && body.items.length !== 1) {
    return "Single trade must contain one item";
  }

  if (!Number.isInteger(body.totalPrice) || body.totalPrice < 1 || body.totalPrice > 2_000_000_000) {
    return "Invalid total price";
  }

  if (
    body.submissionId !== undefined &&
    (typeof body.submissionId !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(body.submissionId))
  ) {
    return "Invalid submission id";
  }

  for (const item of body.items) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return "Invalid trade item";
    }
    const hasNumber = typeof item.characterNumber === "string";
    const hasId = typeof item.characterId === "string";
    if (
      hasNumber === hasId ||
      (hasNumber &&
        (item.characterNumber.length !== 3 || !/^\d{3}$/.test(item.characterNumber))) ||
      (hasId && (item.characterId.length < 1 || item.characterId.length > 100)) ||
      typeof item.rarity !== "string" ||
      item.rarity.length < 1 ||
      item.rarity.length > 50 ||
      typeof item.mutation !== "string" ||
      item.mutation.length < 1 ||
      item.mutation.length > 50 ||
      !Number.isInteger(item.level) ||
      item.level < 1 ||
      item.level > 301 ||
      !Number.isInteger(item.quantity) ||
      item.quantity < 1 ||
      item.quantity > 9999
    ) {
      return "Invalid trade item";
    }
  }

  return null;
}

function validateExchangePayload(body) {
  if (
    !body ||
    typeof body !== "object" ||
    "totalPrice" in body ||
    typeof body.submissionId !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(body.submissionId)
  ) {
    return "Invalid submission id";
  }
  if (
    typeof body.exchangeDescription !== "string" ||
    body.exchangeDescription.trim().length < 1 ||
    body.exchangeDescription.trim().length > 1000
  ) {
    return "Invalid exchange description";
  }
  const tradeType = Array.isArray(body.items) && body.items.length === 1
    ? "single"
    : "bundle";
  const validationError = validatePayload({
    submissionId: body.submissionId,
    tradeType,
    totalPrice: 1,
    items: body.items,
  });
  if (validationError) return validationError;
  if (body.items.some((item) => typeof item.characterNumber !== "string")) {
    return "Invalid trade item";
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
  const row = await env.DB
    .prepare(
      `SELECT
         (SELECT COUNT(*) FROM trades WHERE client_ip = ? AND submitted_at >= ?) +
         (SELECT COUNT(*) FROM exchange_submissions WHERE client_ip = ? AND submitted_at >= ?)
         AS count`
    )
    .bind(ip, since, ip, since)
    .first();
  return Number(row?.count || 0) >= 10;
}

async function createTrade(request, env) {
  const clientIp = getClientIp(request);

  let body;
  try {
    body = await parseJsonBody(request);
  } catch (error) {
    return invalidJsonResponse(request, error);
  }

  const error = validatePayload(body);
  if (error) {
    return json({ ok: false, error }, 400, request);
  }

  try {
    if (await isBlocked(env, clientIp)) {
      return json({ ok: false, error: "Access blocked" }, 403, request);
    }
    if (body.submissionId) {
      const existing = await env.DB
        .prepare("SELECT id, status, submitted_at FROM trades WHERE submission_id = ?")
        .bind(body.submissionId)
        .first();
      if (existing) {
        return json(
          {
            ok: true,
            tradeId: existing.id,
            status: existing.status,
            submittedAt: existing.submitted_at,
            duplicate: true,
          },
          200,
          request
        );
      }
    }
    if (await isRateLimited(env, clientIp)) {
      return json({ ok: false, error: "Too many submissions. Please try again later." }, 429, request);
    }
  } catch (error) {
    console.error("abuse check failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }

  const submittedAt = new Date().toISOString();

  try {
    const resolvedItems = await Promise.all(
      body.items.map(async (item) => {
        if (item.characterId) return item;
        const character = await env.DB
          .prepare(
            `SELECT character_id
             FROM market_character_numbers
             WHERE character_number = ?`
          )
          .bind(item.characterNumber)
          .first();
        if (!character) {
          return null;
        }
        return { ...item, characterId: character.character_id };
      })
    );
    if (resolvedItems.some((item) => item === null)) {
      return json({ ok: false, error: "Character number not found" }, 404, request);
    }

    const parent = env.DB
      .prepare(
        "INSERT INTO trades (trade_type, total_amount, status, submitted_at, client_ip, submission_id) VALUES (?, ?, 'pending', ?, ?, ?)"
      )
      .bind(body.tradeType, body.totalPrice, submittedAt, clientIp, body.submissionId || null);

    const statements = [
      parent,
      ...resolvedItems.map((item, index) =>
        env.DB
          .prepare(
            "INSERT INTO trade_characters (trade_id, position, character_id, rarity, level, mutation, quantity, is_verified) VALUES (last_insert_rowid(), ?, ?, ?, ?, ?, ?, 0)"
          )
          .bind(
            index + 1,
            item.characterId,
            item.rarity,
            item.level,
            item.mutation,
            item.quantity
          )
      ),
    ];

    const results = await env.DB.batch(statements);
    const tradeId = results?.[0]?.meta?.last_row_id;
    if (!tradeId) {
      throw new Error("D1 did not return a trade id");
    }

    return json(
      {
        ok: true,
        tradeId,
        status: "pending",
        submittedAt,
      },
      201,
      request
    );
  } catch (error) {
    if (body.submissionId) {
      try {
        const existing = await env.DB
          .prepare("SELECT id, status, submitted_at FROM trades WHERE submission_id = ?")
          .bind(body.submissionId)
          .first();
        if (existing) {
          return json(
            {
              ok: true,
              tradeId: existing.id,
              status: existing.status,
              submittedAt: existing.submitted_at,
              duplicate: true,
            },
            200,
            request
          );
        }
      } catch (lookupError) {
        console.error("submission retry lookup failed", lookupError);
      }
    }
    console.error("createTrade failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function createExchangeSubmission(request, env) {
  const clientIp = getClientIp(request);
  let body;
  try {
    body = await parseJsonBody(request);
  } catch (error) {
    return invalidJsonResponse(request, error);
  }

  const error = validateExchangePayload(body);
  if (error) {
    return json({ ok: false, error }, 400, request);
  }

  try {
    if (await isBlocked(env, clientIp)) {
      return json({ ok: false, error: "Access blocked" }, 403, request);
    }
    const existing = await env.DB
      .prepare(
        "SELECT id, status, submitted_at FROM exchange_submissions WHERE submission_id = ?"
      )
      .bind(body.submissionId)
      .first();
    if (existing) {
      return json(
        {
          ok: true,
          exchangeId: existing.id,
          status: existing.status,
          submittedAt: existing.submitted_at,
          duplicate: true,
        },
        200,
        request
      );
    }
    if (await isRateLimited(env, clientIp)) {
      return json({ ok: false, error: "Too many submissions. Please try again later." }, 429, request);
    }
  } catch (error) {
    console.error("exchange abuse check failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }

  try {
    const resolvedItems = await Promise.all(
      body.items.map(async (item) => {
        if (item.characterId) return item;
        const character = await env.DB
          .prepare(
            `SELECT character_id
             FROM market_character_numbers
             WHERE character_number = ?`
          )
          .bind(item.characterNumber)
          .first();
        return character ? { ...item, characterId: character.character_id } : null;
      })
    );
    if (resolvedItems.some((item) => item === null)) {
      return json({ ok: false, error: "Character number not found" }, 404, request);
    }

    const submittedAt = new Date().toISOString();
    const statements = [
      env.DB.prepare(
        `INSERT INTO exchange_submissions
         (submission_id, exchange_description, status, submitted_at, client_ip)
         VALUES (?, ?, 'pending', ?, ?)`
      ).bind(
        body.submissionId,
        body.exchangeDescription.trim(),
        submittedAt,
        clientIp
      ),
      ...resolvedItems.map((item, index) =>
        env.DB.prepare(
          `INSERT INTO exchange_submission_characters
           (submission_id, position, character_id, rarity, level, mutation, quantity)
           VALUES (last_insert_rowid(), ?, ?, ?, ?, ?, ?)`
        ).bind(
          index + 1,
          item.characterId,
          item.rarity,
          item.level,
          item.mutation,
          item.quantity
        )
      ),
    ];
    const results = await env.DB.batch(statements);
    const exchangeId = results?.[0]?.meta?.last_row_id;
    if (!exchangeId) throw new Error("D1 did not return an exchange id");
    return json(
      { ok: true, exchangeId, status: "pending", submittedAt },
      201,
      request
    );
  } catch (error) {
    try {
      const existing = await env.DB
        .prepare(
          "SELECT id, status, submitted_at FROM exchange_submissions WHERE submission_id = ?"
        )
        .bind(body.submissionId)
        .first();
      if (existing) {
        return json(
          {
            ok: true,
            exchangeId: existing.id,
            status: existing.status,
            submittedAt: existing.submitted_at,
            duplicate: true,
          },
          200,
          request
        );
      }
    } catch (lookupError) {
      console.error("exchange retry lookup failed", lookupError);
    }
    console.error("createExchangeSubmission failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function getCharacters(request, env) {
  const result = await env.DB
    .prepare(
      `SELECT numbers.character_number, names.character_id, names.character_name
       FROM market_character_numbers AS numbers
       INNER JOIN market_character_names AS names
         ON names.character_id = numbers.character_id
       ORDER BY numbers.character_number`
    )
    .all();

  const rarities = {
    c: "common",
    r: "rare",
    e: "epic",
    l: "legend",
    m: "mythic",
    s: "secret",
    u: "ultimate",
    x: "emperor",
  };
  const characters = (result.results || []).map((row) => ({
    characterNumber: row.character_number,
    characterId: row.character_id,
    characterName: row.character_name,
    rarity: rarities[String(row.character_id).slice(0, 1)] || null,
  }));

  return json({ ok: true, characters }, 200, request);
}

async function getPendingExchanges(request, env) {
  if (!isReviewer(request, env)) return unauthorized(request);
  const url = new URL(request.url);
  const rawLimit = Number(url.searchParams.get("limit") || "50");
  const limit = Number.isInteger(rawLimit) ? Math.min(Math.max(rawLimit, 1), 100) : 50;
  const rawAfter = url.searchParams.get("after") || "0";
  if (!/^\d+$/.test(rawAfter) || !Number.isSafeInteger(Number(rawAfter))) {
    return json({ ok: false, error: "Invalid cursor" }, 400, request);
  }
  const after = Number(rawAfter);
  const rawThrough = url.searchParams.get("through");
  const status = url.searchParams.get("status") || "pending";
  if (!["pending", "approved", "rejected"].includes(status)) {
    return json({ ok: false, error: "Invalid status" }, 400, request);
  }
  if (
    rawThrough !== null &&
    (!/^\d+$/.test(rawThrough) || !Number.isSafeInteger(Number(rawThrough)))
  ) {
    return json({ ok: false, error: "Invalid cursor" }, 400, request);
  }

  try {
    const through = rawThrough === null
      ? Number(
        (await env.DB.prepare(
          "SELECT COALESCE(MAX(id), 0) AS max_id FROM exchange_submissions"
        ).first())?.max_id || 0
      )
      : Number(rawThrough);
    if (!Number.isSafeInteger(through) || through < 0 || after > through) {
      return json({ ok: false, error: "Invalid cursor" }, 400, request);
    }
    const page = await env.DB.prepare(
      `SELECT id, exchange_description, status, submitted_at,
              reviewed_by, reviewed_at
       FROM exchange_submissions
       WHERE status = ? AND id > ? AND id <= ?
       ORDER BY id ASC
       LIMIT ?`
    ).bind(status, after, through, limit + 1).all();
    const pageRows = page.results || [];
    const hasMore = pageRows.length > limit;
    const selected = pageRows.slice(0, limit);
    const exchanges = new Map(selected.map((row) => [
      row.id,
      {
        id: row.id,
        exchangeDescription: row.exchange_description,
        status: row.status,
        submittedAt: row.submitted_at,
        reviewedBy: row.reviewed_by || null,
        reviewedAt: row.reviewed_at || null,
        items: [],
      },
    ]));

    if (selected.length > 0) {
      const ids = selected.map((row) => row.id);
      const details = await env.DB.prepare(
        `SELECT ec.submission_id, ec.position, ec.character_id, ec.rarity,
                ec.level, ec.mutation, ec.quantity,
                numbers.character_number, names.character_name
         FROM exchange_submission_characters ec
         LEFT JOIN market_character_numbers numbers
           ON numbers.character_id = ec.character_id
         LEFT JOIN market_character_names names
           ON names.character_id = ec.character_id
         WHERE ec.submission_id IN (${ids.map(() => "?").join(", ")})
         ORDER BY ec.submission_id ASC, ec.position ASC`
      ).bind(...ids).all();

      for (const row of details.results || []) {
        exchanges.get(row.submission_id)?.items.push({
          characterId: row.character_id,
          characterNumber: row.character_number || null,
          characterName: row.character_name || null,
          rarity: row.rarity,
          level: row.level,
          mutation: row.mutation,
          quantity: row.quantity,
        });
      }
    }
    return json(
      {
        ok: true,
        exchanges: [...exchanges.values()],
        nextCursor: hasMore ? selected.at(-1).id : null,
        throughCursor: through,
        hasMore,
      },
      200,
      request
    );
  } catch (error) {
    console.error("getPendingExchanges failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function getAdminStatistics(request, env) {
  if (!isReviewer(request, env)) return unauthorized(request);

  try {
    const [tradeStatuses, exchangeStatuses, characterRows, totals] = await Promise.all([
      env.DB.prepare(
        "SELECT status, COUNT(*) AS count FROM trades GROUP BY status"
      ).all(),
      env.DB.prepare(
        "SELECT status, COUNT(*) AS count FROM exchange_submissions GROUP BY status"
      ).all(),
      env.DB.prepare(
        `SELECT tc.character_id AS character_id,
                COUNT(DISTINCT t.id) AS trade_count,
                SUM(tc.quantity) AS item_count,
                MAX(t.submitted_at) AS last_submitted_at
         FROM trade_characters tc
         INNER JOIN trades t ON t.id = tc.trade_id
         WHERE t.status = 'approved' AND tc.is_verified = 1
         GROUP BY tc.character_id
         ORDER BY trade_count DESC, item_count DESC, tc.character_id ASC`
      ).all(),
      env.DB.prepare(
        `SELECT
           (SELECT COUNT(*) FROM trades) AS total_trades,
           (SELECT COUNT(*) FROM exchange_submissions) AS total_exchanges,
           (SELECT COUNT(DISTINCT character_id) FROM trade_characters tc
             INNER JOIN trades t ON t.id = tc.trade_id
             WHERE t.status = 'approved' AND tc.is_verified = 1) AS character_count,
           (SELECT COUNT(*) FROM trade_characters tc
             INNER JOIN trades t ON t.id = tc.trade_id
             WHERE t.status = 'approved' AND tc.is_verified = 1) AS verified_items`
      ).first(),
    ]);

    const toCounts = (rows) => Object.fromEntries(
      (rows.results || []).map((row) => [row.status, Number(row.count || 0)])
    );
    return json({
      ok: true,
      generatedAt: new Date().toISOString(),
      trades: {
        total: Number(totals?.total_trades || 0),
        pending: toCounts(tradeStatuses).pending || 0,
        approved: toCounts(tradeStatuses).approved || 0,
        rejected: toCounts(tradeStatuses).rejected || 0,
      },
      exchanges: {
        total: Number(totals?.total_exchanges || 0),
        pending: toCounts(exchangeStatuses).pending || 0,
        approved: toCounts(exchangeStatuses).approved || 0,
        rejected: toCounts(exchangeStatuses).rejected || 0,
      },
      verifiedItems: Number(totals?.verified_items || 0),
      characterCount: Number(totals?.character_count || 0),
      characters: (characterRows.results || []).map((row) => ({
        characterId: row.character_id,
        tradeCount: Number(row.trade_count || 0),
        itemCount: Number(row.item_count || 0),
        lastSubmittedAt: row.last_submitted_at || null,
      })),
    }, 200, request);
  } catch (error) {
    console.error("getAdminStatistics failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function getTrades(request, env) {
  if (!isReviewer(request, env)) return unauthorized(request);
  const url = new URL(request.url);
  const rawLimit = Number(url.searchParams.get("limit") || "50");
  const limit = Number.isInteger(rawLimit) ? Math.min(Math.max(rawLimit, 1), 100) : 50;
  const status = url.searchParams.get("status");

  if (status && !["pending", "approved", "rejected"].includes(status)) {
    return json({ ok: false, error: "Invalid status" }, 400, request);
  }

  const isStatusQueue = Boolean(status);
  const rawAfter = url.searchParams.get("after") || "0";
  const rawThrough = url.searchParams.get("through");
  if (
    isStatusQueue &&
    (
      !/^\d+$/.test(rawAfter) ||
      !Number.isSafeInteger(Number(rawAfter)) ||
      (rawThrough !== null &&
        (!/^\d+$/.test(rawThrough) || !Number.isSafeInteger(Number(rawThrough))))
    )
  ) {
    return json({ ok: false, error: "Invalid cursor" }, 400, request);
  }
  const after = Number(rawAfter);

  try {
    const filters = [];
    const bindings = [];
    let through = null;
    if (isStatusQueue) {
      through = rawThrough === null
        ? Number(
          (await env.DB.prepare(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM trades"
          ).first())?.max_id || 0
        )
        : Number(rawThrough);
      if (!Number.isSafeInteger(through) || through < 0 || after > through) {
        return json({ ok: false, error: "Invalid cursor" }, 400, request);
      }
      filters.push("status = ?", "id > ?", "id <= ?");
      bindings.push(status, after, through);
    }
    const where = filters.length > 0 ? `WHERE ${filters.join(" AND ")}` : "";
    const pageSize = isStatusQueue ? limit + 1 : limit;
    const order = isStatusQueue ? "ASC" : "DESC";
    const parentResult = await env.DB.prepare(
      `SELECT id, trade_type, total_amount, status, submitted_at,
              reviewed_by, reviewed_at
       FROM trades
       ${where}
       ORDER BY id ${order}
       LIMIT ?`
    ).bind(...bindings, pageSize).all();
    const parentRows = parentResult.results || [];
    const hasMore = isStatusQueue && parentRows.length > limit;
    const selected = parentRows.slice(0, limit);
    const trades = new Map(selected.map((row) => [
      row.id,
      {
        id: row.id,
        tradeType: row.trade_type,
        totalPrice: row.total_amount,
        status: row.status,
        submittedAt: row.submitted_at,
        reviewedBy: row.reviewed_by || null,
        reviewedAt: row.reviewed_at || null,
        items: [],
      },
    ]));

    if (selected.length > 0) {
      const ids = selected.map((row) => row.id);
      const detailResult = await env.DB.prepare(
        `SELECT tc.trade_id, tc.position, tc.character_id, tc.rarity,
                tc.level, tc.mutation, tc.quantity, tc.is_verified,
                numbers.character_number, names.character_name
         FROM trade_characters tc
         LEFT JOIN market_character_names names
           ON names.character_id = tc.character_id
         LEFT JOIN market_character_numbers numbers
           ON numbers.character_id = tc.character_id
         WHERE tc.trade_id IN (${ids.map(() => "?").join(", ")})
         ORDER BY tc.trade_id ASC, tc.position ASC`
      ).bind(...ids).all();

      for (const row of detailResult.results || []) {
        trades.get(row.trade_id)?.items.push({
          position: row.position,
          characterId: row.character_id,
          characterNumber: row.character_number || null,
          characterName: row.character_name || null,
          rarity: row.rarity,
          level: row.level,
          mutation: row.mutation,
          quantity: row.quantity,
          isVerified: row.is_verified,
        });
      }
    }

    return json(
      {
        ok: true,
        trades: [...trades.values()],
        nextCursor: hasMore ? selected.at(-1).id : null,
        throughCursor: through,
        hasMore,
      },
      200,
      request
    );
  } catch (error) {
    console.error("getTrades failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function updateExchangeStatus(request, env, exchangeId) {
  if (!isReviewer(request, env)) return unauthorized(request);
  if (!/^\d+$/.test(exchangeId)) {
    return json({ ok: false, error: "Invalid exchange id" }, 400, request);
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "Invalid JSON" }, 400, request);
  }
  if (!["approved", "rejected"].includes(body?.status)) {
    return json({ ok: false, error: "Invalid status" }, 400, request);
  }
  const reviewerId =
    typeof body.reviewerId === "string" ? body.reviewerId.trim() : "";
  if (!reviewerId || reviewerId.length > 100) {
    return json({ ok: false, error: "Invalid reviewerId" }, 400, request);
  }
  const id = Number(exchangeId);
  if (!Number.isSafeInteger(id)) {
    return json({ ok: false, error: "Invalid exchange id" }, 400, request);
  }
  const reviewedAt = new Date().toISOString();
  const reviewToken = crypto.randomUUID();
  try {
    const results = await env.DB.batch([
      env.DB.prepare(
        `UPDATE exchange_submissions
         SET status = ?, reviewed_by = ?, reviewed_at = ?, review_token = ?
         WHERE id = ? AND status = 'pending'`
      ).bind(body.status, reviewerId, reviewedAt, reviewToken, id),
      env.DB.prepare(
        `INSERT INTO exchange_review_actions (submission_id, status, reviewer_id, reviewed_at)
         SELECT id, status, reviewed_by, reviewed_at
         FROM exchange_submissions
         WHERE id = ? AND status = ? AND review_token = ?`
      ).bind(id, body.status, reviewToken),
    ]);
    if (Number(results?.[0]?.meta?.changes || 0) !== 1) {
      const exchange = await env.DB
        .prepare("SELECT status FROM exchange_submissions WHERE id = ?")
        .bind(id)
        .first();
      if (!exchange) return json({ ok: false, error: "Exchange not found" }, 404, request);
      return json(
        { ok: false, error: "Exchange has already been reviewed" },
        409,
        request
      );
    }
    return json(
      { ok: true, exchangeId: id, status: body.status, reviewedAt },
      200,
      request
    );
  } catch (error) {
    console.error("updateExchangeStatus failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
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
  if (!ip || ip.length > 45 || !["block", "unblock"].includes(action)) {
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
  if (!isReviewer(request, env)) return unauthorized(request);
  if (!/^\d+$/.test(tradeId)) {
    return json({ ok: false, error: "Invalid trade id" }, 400, request);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "Invalid JSON" }, 400, request);
  }

  if (!["approved", "rejected"].includes(body?.status)) {
    return json({ ok: false, error: "Invalid status" }, 400, request);
  }
  const reviewerId =
    typeof body.reviewerId === "string" ? body.reviewerId.trim() : "";
  if (!reviewerId || reviewerId.length > 100) {
    return json({ ok: false, error: "Invalid reviewerId" }, 400, request);
  }

  const id = Number(tradeId);
  if (!Number.isSafeInteger(id)) {
    return json({ ok: false, error: "Invalid trade id" }, 400, request);
  }
  const reviewedAt = new Date().toISOString();
  const reviewToken = crypto.randomUUID();

  try {
    const verified = body.status === "approved" ? 1 : 0;
    const results = await env.DB.batch([
      env.DB
        .prepare(
          `UPDATE trades
           SET status = ?, reviewed_by = ?, reviewed_at = ?, review_token = ?
           WHERE id = ? AND status = 'pending'`
        )
        .bind(body.status, reviewerId, reviewedAt, reviewToken, id),
      env.DB
        .prepare(
          `UPDATE trade_characters
           SET is_verified = ?
           WHERE trade_id = ?
             AND EXISTS (
               SELECT 1 FROM trades
               WHERE id = ? AND status = ? AND review_token = ?
             )`
        )
        .bind(verified, id, id, body.status, reviewToken),
      env.DB
        .prepare(
          `INSERT INTO trade_review_actions (trade_id, status, reviewer_id, reviewed_at)
           SELECT id, status, reviewed_by, reviewed_at
           FROM trades
           WHERE id = ? AND status = ? AND review_token = ?`
        )
        .bind(id, body.status, reviewToken),
    ]);
    if (Number(results?.[0]?.meta?.changes || 0) !== 1) {
      const trade = await env.DB
        .prepare("SELECT status FROM trades WHERE id = ?")
        .bind(id)
        .first();
      if (!trade) {
        return json({ ok: false, error: "Trade not found" }, 404, request);
      }
      return json(
        { ok: false, error: "Trade has already been reviewed" },
        409,
        request
      );
    }

    return json(
      { ok: true, tradeId: id, status: body.status, reviewedAt },
      200,
      request
    );
  } catch (error) {
    console.error("updateTradeStatus failed", error);
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
          AND tc.is_verified = 1
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
      AND tc.is_verified = 1
      AND tc.mutation IN ('normal', 'ノーマル')
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

export function calculateDemandScore(characterTradeCount, totalTradeCount, recentTradeCount) {
  const counts = [characterTradeCount, totalTradeCount, recentTradeCount].map(Number);
  if (counts.some((count) => !Number.isFinite(count) || count < 0)) {
    throw new RangeError("Trade counts must be finite, non-negative numbers");
  }
  const [characterCount, totalCount, recentCount] = counts;
  const shareScore = totalCount > 0 ? (characterCount / totalCount) * 5 : 0;
  const recentScore = Math.min(recentCount / 5, 5);
  return Math.round(
    Math.max(0, Math.min(5, shareScore * 0.6 + recentScore * 0.4)) * 10
  ) / 10;
}

async function createMarketSnapshot(env) {
  const chars = await env.DB.prepare(
    `SELECT DISTINCT tc.character_id
     FROM trade_characters tc
     INNER JOIN trades t ON t.id = tc.trade_id
     WHERE t.status = 'approved' AND tc.is_verified = 1`
  ).all();

  const createdAt = new Date().toISOString();
  const snapshots = [];

  for (const row of chars.results || []) {
    const characterId = row.character_id;
    const lv1 = await calculateMarketForCharacter(env, characterId, 1);
    const lvMax = await calculateMarketForCharacter(env, characterId, 301);

    const tradeCount = await env.DB.prepare(
      `SELECT COUNT(DISTINCT t.id) AS count
       FROM trade_characters tc
       INNER JOIN trades t ON t.id = tc.trade_id
       WHERE t.status = 'approved' AND tc.is_verified = 1 AND tc.character_id = ?`
    ).bind(characterId).first();

    const totalCount = await env.DB.prepare(
      `SELECT COUNT(*) AS count
       FROM (
         SELECT DISTINCT tc.character_id, t.id
         FROM trade_characters tc
         INNER JOIN trades t ON t.id = tc.trade_id
         WHERE t.status = 'approved' AND tc.is_verified = 1
       ) AS character_trades`
    ).first();

    const since = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    const recentCount = await env.DB.prepare(
      `SELECT COUNT(DISTINCT t.id) AS count
       FROM trade_characters tc
       INNER JOIN trades t ON t.id = tc.trade_id
       WHERE t.status = 'approved'
         AND tc.is_verified = 1
         AND tc.character_id = ?
         AND t.submitted_at >= ?`
    ).bind(characterId, since).first();

    const count = Number(tradeCount?.count || 0);
    const total = Number(totalCount?.count || 0);
    const recent = Number(recentCount?.count || 0);
    const demandScore = calculateDemandScore(count, total, recent);
    const mutationRows = await env.DB.prepare(
      `WITH per_trade AS (
         SELECT t.id AS trade_id,
                CASE
                  WHEN tc.mutation IN ('normal', 'ノーマル') THEN 'normal'
                  ELSE tc.mutation
                END AS mutation,
                tc.level,
                AVG(CAST(t.total_amount AS REAL) / tc.quantity) AS unit_price
         FROM trade_characters tc
         INNER JOIN trades t ON t.id = tc.trade_id
         WHERE t.status = 'approved'
           AND tc.character_id = ?
           AND tc.is_verified = 1
           AND t.trade_type = 'single'
         GROUP BY t.id, mutation, tc.level
       ),
       per_level AS (
         SELECT mutation, level, COUNT(*) AS sample_count,
                AVG(unit_price) AS avg_unit_price
         FROM per_trade
         GROUP BY mutation, level
       ),
       normal_levels AS (
         SELECT level, sample_count, avg_unit_price
         FROM per_level
         WHERE mutation = 'normal'
       ),
       matched_mutations AS (
         SELECT mutations.mutation,
                MIN(mutations.sample_count, normal_levels.sample_count) AS sample_count,
                mutations.avg_unit_price / NULLIF(normal_levels.avg_unit_price, 0) AS ratio
         FROM per_level AS mutations
         INNER JOIN normal_levels ON normal_levels.level = mutations.level
         WHERE mutations.mutation <> 'normal'
       ),
       matched_totals AS (
         SELECT mutation, SUM(sample_count) AS sample_count,
                SUM(ratio * sample_count) / SUM(sample_count) AS multiplier
         FROM matched_mutations
         GROUP BY mutation
       ),
       mutation_names AS (
         SELECT DISTINCT mutation
         FROM per_level
         WHERE mutation <> 'normal'
       )
       SELECT mutation_names.mutation,
              COALESCE(matched_totals.sample_count, 0) AS sample_count,
              CASE WHEN matched_totals.sample_count >= 3
                   THEN matched_totals.multiplier ELSE NULL END AS multiplier
       FROM mutation_names
       LEFT JOIN matched_totals ON matched_totals.mutation = mutation_names.mutation
       UNION ALL
       SELECT 'normal' AS mutation, SUM(sample_count) AS sample_count,
              CASE WHEN SUM(sample_count) >= 3 THEN 1.0 ELSE NULL END AS multiplier
       FROM per_level
       WHERE mutation = 'normal'
       HAVING COUNT(*) > 0`
    ).bind(characterId).all();
    const statements = [
      env.DB.prepare(
        `INSERT INTO market_snapshots
         (character_id, level1_value, level_max_value, demand_score, sample_count,
          created_at, level1_sample_count, level_max_sample_count)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
      ).bind(
        characterId,
        lv1.value,
        lvMax.value,
        demandScore,
        count,
        createdAt,
        lv1.sampleCount,
        lvMax.sampleCount
      ),
      ...(mutationRows.results || []).map((row) => {
        const multiplier = row.multiplier == null
          ? null
          : Math.round(Number(row.multiplier) * 100) / 100;
        return env.DB.prepare(
          `INSERT INTO market_snapshot_mutations
           (snapshot_id, mutation, multiplier, sample_count)
           SELECT id, ?, ?, ?
           FROM market_snapshots
           WHERE character_id = ? AND created_at = ?
           ORDER BY id DESC
           LIMIT 1`
        ).bind(
          row.mutation,
          multiplier,
          Number(row.sample_count || 0),
          characterId,
          createdAt
        );
      }),
    ];
    const results = await env.DB.batch(statements);
    const snapshotId = results?.[0]?.meta?.last_row_id || null;

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

async function getMarketSnapshot(request, env) {
  const url = new URL(request.url);
  const characterName = url.searchParams.get("characterName")?.trim() || "";
  const characterNumber = url.searchParams.get("characterNumber")?.trim() || "";
  if (Boolean(characterName) === Boolean(characterNumber)) {
    return json(
      { ok: false, error: "Provide exactly one of characterName or characterNumber" },
      400,
      request
    );
  }

  let character;
  if (characterNumber) {
    if (!/^\d{3}$/.test(characterNumber)) {
      return json({ ok: false, error: "Invalid characterNumber" }, 400, request);
    }
    character = await env.DB
      .prepare(
        `SELECT numbers.character_number, names.character_id, names.character_name
         FROM market_character_numbers AS numbers
         INNER JOIN market_character_names AS names
           ON names.character_id = numbers.character_id
         WHERE numbers.character_number = ?`
      )
      .bind(characterNumber)
      .first();
    if (!character) {
      return json({ ok: false, error: "Character number not found" }, 404, request);
    }
  } else {
    if (characterName.length > 100) {
      return json({ ok: false, error: "Invalid characterName" }, 400, request);
    }
    character = await env.DB
      .prepare(
        "SELECT character_id, character_name FROM market_character_names WHERE character_name = ?"
      )
      .bind(characterName)
      .first();
  }
  if (!character) {
    return json({ ok: false, error: "Character not found" }, 404, request);
  }

  const snapshot = await env.DB
    .prepare(
      `SELECT id, character_id, level1_value, level_max_value, demand_score,
              sample_count, created_at, level1_sample_count, level_max_sample_count
       FROM market_snapshots
       WHERE character_id = ?
       ORDER BY created_at DESC, id DESC
       LIMIT 1`
    )
    .bind(character.character_id)
    .first();
  if (!snapshot) {
    return json({ ok: false, error: "Market snapshot not found" }, 404, request);
  }

  const mutationResult = await env.DB
    .prepare(
      `SELECT mutation, multiplier, sample_count
       FROM market_snapshot_mutations
       WHERE snapshot_id = ?
       ORDER BY mutation`
    )
    .bind(snapshot.id)
    .all();
  const changes = await getMarketSnapshotComparison(
    env,
    snapshot.character_id,
    snapshot.id
  );

  return json(
    {
      ok: true,
      characterNumber: character.character_number || null,
      characterId: snapshot.character_id,
      characterName: character.character_name,
      level1Value: snapshot.level1_value,
      levelMaxValue: snapshot.level_max_value,
      demandScore: snapshot.demand_score,
      sampleCount: Number(snapshot.sample_count || 0),
      level1SampleCount: snapshot.level1_sample_count,
      levelMaxSampleCount: snapshot.level_max_sample_count,
      createdAt: snapshot.created_at,
      changes: {
        level1Direction: changes.level1Direction,
        levelMaxDirection: changes.levelMaxDirection,
        demandDirection: changes.demandDirection,
        mutationDirections: changes.mutationDirections,
        hasPrevious: changes.hasPrevious,
      },
      mutations: (mutationResult.results || []).map((row) => ({
        mutation: row.mutation,
        multiplier: row.multiplier,
        sampleCount: Number(row.sample_count || 0),
        direction: changes.mutationDirections[row.mutation] || "→",
      })),
    },
    200,
    request
  );
}

async function resolveMarketCharacterNumber(request, env) {
  const url = new URL(request.url);
  const characterNumber = url.searchParams.get("number")?.trim() || "";
  if (!/^\d{3}$/.test(characterNumber)) {
    return json({ ok: false, error: "Invalid character number" }, 400, request);
  }

  const character = await env.DB
    .prepare(
      `SELECT numbers.character_number, names.character_id, names.character_name
       FROM market_character_numbers AS numbers
       INNER JOIN market_character_names AS names
         ON names.character_id = numbers.character_id
       WHERE numbers.character_number = ?`
    )
    .bind(characterNumber)
    .first();
  if (!character) {
    return json({ ok: false, error: "Character number not found" }, 404, request);
  }

  return json(
    {
      ok: true,
      characterNumber: character.character_number,
      characterId: character.character_id,
      characterName: character.character_name,
    },
    200,
    request
  );
}

async function getTrade(request, env, tradeId) {
  if (!/^\d+$/.test(tradeId)) {
    return json({ ok: false, error: "Invalid trade id" }, 400, request);
  }
  const id = Number(tradeId);
  if (!Number.isSafeInteger(id)) {
    return json({ ok: false, error: "Invalid trade id" }, 400, request);
  }

  const trade = await env.DB
    .prepare(
      "SELECT id, trade_type, total_amount, status, submitted_at FROM trades WHERE id = ?"
    )
    .bind(id)
    .first();

  if (!trade) {
    return json({ ok: false, error: "Trade not found" }, 404, request);
  }

  const result = await env.DB
    .prepare(
      "SELECT position, character_id, rarity, level, mutation, quantity, is_verified FROM trade_characters WHERE trade_id = ? ORDER BY position"
    )
    .bind(id)
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

      if (request.method === "POST" && url.pathname === "/trades") {
        return createTrade(request, env);
      }

      if (request.method === "POST" && url.pathname === "/exchanges") {
        return createExchangeSubmission(request, env);
      }

      if (request.method === "GET" && url.pathname === "/exchanges") {
        return getPendingExchanges(request, env);
      }

      const exchangeStatusMatch = url.pathname.match(/^\/exchanges\/(\d+)\/status$/);
      if (request.method === "POST" && exchangeStatusMatch) {
        return updateExchangeStatus(request, env, exchangeStatusMatch[1]);
      }

      const statusMatch = url.pathname.match(/^\/trades\/(\d+)\/status$/);
      if (request.method === "POST" && statusMatch) {
        return updateTradeStatus(request, env, statusMatch[1]);
      }

      if (request.method === "GET" && url.pathname === "/trades") {
        return getTrades(request, env);
      }

      if (request.method === "GET" && url.pathname === "/market-value") {
        return getMarketValue(request, env);
      }

      if (request.method === "GET" && url.pathname === "/market-snapshot") {
        return getMarketSnapshot(request, env);
      }

      if (request.method === "GET" && url.pathname === "/characters") {
        return getCharacters(request, env);
      }

      if (request.method === "GET" && url.pathname === "/characters/resolve") {
        return resolveMarketCharacterNumber(request, env);
      }

      if (request.method === "GET" && url.pathname === "/admin/statistics") {
        return getAdminStatistics(request, env);
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
