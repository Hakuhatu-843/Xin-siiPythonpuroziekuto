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

  for (const item of body.items) {
    if (
      typeof item.characterId !== "string" ||
      item.characterId.length < 1 ||
      item.characterId.length > 100 ||
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
    .prepare("SELECT COUNT(*) AS count FROM trades WHERE client_ip = ? AND submitted_at >= ?")
    .bind(ip, since)
    .first();
  return Number(row?.count || 0) >= 10;
}

async function createTrade(request, env) {
  const clientIp = getClientIp(request);

  try {
    if (await isBlocked(env, clientIp)) {
      return json({ ok: false, error: "Access blocked" }, 403, request);
    }
    if (await isRateLimited(env, clientIp)) {
      return json({ ok: false, error: "Too many submissions. Please try again later." }, 429, request);
    }
  } catch (error) {
    console.error("abuse check failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "Invalid JSON" }, 400, request);
  }

  const error = validatePayload(body);
  if (error) {
    return json({ ok: false, error }, 400, request);
  }

  const submittedAt = new Date().toISOString();

  try {
    const parent = env.DB
      .prepare(
        "INSERT INTO trades (trade_type, total_amount, status, submitted_at, client_ip) VALUES (?, ?, 'pending', ?, ?)"
      )
      .bind(body.tradeType, body.totalPrice, submittedAt, clientIp);

    const statements = [
      parent,
      ...body.items.map((item, index) =>
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
    console.error("createTrade failed", error);
    return json({ ok: false, error: "Database error" }, 500, request);
  }
}

async function getTrades(request, env) {
  if (!isAdmin(request, env)) return unauthorized(request);
  const url = new URL(request.url);
  const rawLimit = Number(url.searchParams.get("limit") || "50");
  const limit = Number.isInteger(rawLimit) ? Math.min(Math.max(rawLimit, 1), 100) : 50;
  const status = url.searchParams.get("status");

  if (status && !["pending", "approved", "rejected"].includes(status)) {
    return json({ ok: false, error: "Invalid status" }, 400, request);
  }

  const where = status ? "WHERE t.status = ?" : "";
  const query = `
    SELECT
      t.id,
      t.trade_type,
      t.total_amount,
      t.status,
      t.submitted_at,
      t.client_ip,
      tc.position,
      tc.character_id,
      tc.rarity,
      tc.level,
      tc.mutation,
      tc.quantity,
      tc.is_verified
    FROM trades t
    LEFT JOIN trade_characters tc ON tc.trade_id = t.id
    ${where}
    ORDER BY t.id DESC, tc.position ASC
    LIMIT ?
  `;

  try {
    const bindings = status ? [status, limit * 3] : [limit * 3];
    const result = await env.DB.prepare(query).bind(...bindings).all();

    const trades = new Map();

    for (const row of result.results || []) {
      if (!trades.has(row.id)) {
        trades.set(row.id, {
          id: row.id,
          tradeType: row.trade_type,
          totalPrice: row.total_amount,
          status: row.status,
          submittedAt: row.submitted_at,
          clientIp: row.client_ip,
          items: [],
        });
      }

      if (row.position !== null) {
        trades.get(row.id).items.push({
          position: row.position,
          characterId: row.character_id,
          rarity: row.rarity,
          level: row.level,
          mutation: row.mutation,
          quantity: row.quantity,
          isVerified: row.is_verified,
        });
      }

      if (trades.size >= limit) {
        const lastId = row.id;
        const sameTradeRows = result.results.filter((r) => r.id === lastId);
        if (sameTradeRows.length > 0) continue;
      }
    }

    return json(
      { ok: true, trades: Array.from(trades.values()).slice(0, limit) },
      200,
      request
    );
  } catch (error) {
    console.error("getTrades failed", error);
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
  if (!isAdmin(request, env)) return unauthorized(request);
  if (!/^\d+$/.test(tradeId)) {
    return json({ ok: false, error: "Invalid trade id" }, 400, request);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "Invalid JSON" }, 400, request);
  }

  if (!["pending", "approved", "rejected"].includes(body?.status)) {
    return json({ ok: false, error: "Invalid status" }, 400, request);
  }

  const id = Number(tradeId);

  try {
    const trade = await env.DB
      .prepare("SELECT id FROM trades WHERE id = ?")
      .bind(id)
      .first();

    if (!trade) {
      return json({ ok: false, error: "Trade not found" }, 404, request);
    }

    const verified = body.status === "approved" ? 1 : 0;

    await env.DB.batch([
      env.DB
        .prepare("UPDATE trades SET status = ? WHERE id = ?")
        .bind(body.status, id),
      env.DB
        .prepare("UPDATE trade_characters SET is_verified = ? WHERE trade_id = ?")
        .bind(verified, id),
    ]);

    return json(
      { ok: true, tradeId: id, status: body.status },
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

    const snapshot = await env.DB.prepare(
      `INSERT INTO market_snapshots
       (character_id, level1_value, level_max_value, demand_score, sample_count, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`
    ).bind(
      characterId,
      lv1.value,
      lvMax.value,
      null,
      Number(tradeCount?.count || 0),
      createdAt
    ).run();

    const snapshotId = snapshot.meta?.last_row_id;
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
