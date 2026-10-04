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
    "Access-Control-Allow-Headers": "Content-Type",
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

async function createTrade(request, env) {
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
        "INSERT INTO trades (trade_type, total_amount, status, submitted_at) VALUES (?, ?, 'pending', ?)"
      )
      .bind(body.tradeType, body.totalPrice, submittedAt);

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
