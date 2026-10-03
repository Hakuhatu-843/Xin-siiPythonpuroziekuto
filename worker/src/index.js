const ALLOWED_ORIGINS = new Set([
  "https://hakuhatu-843.github.io",
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
    const tradeResult = await env.DB
      .prepare(
        "INSERT INTO trades (trade_type, total_amount, status, submitted_at) VALUES (?, ?, 'pending', ?)"
      )
      .bind(body.tradeType, body.totalPrice, submittedAt)
      .run();

    const tradeId = tradeResult.meta.last_row_id;
    if (!tradeId) {
      throw new Error("D1 did not return a trade id");
    }

    const statements = body.items.map((item, index) =>
      env.DB
        .prepare(
          "INSERT INTO trade_characters (trade_id, position, character_id, rarity, level, mutation, quantity, is_verified) VALUES (?, ?, ?, ?, ?, ?, ?, 0)"
        )
        .bind(
          tradeId,
          index + 1,
          item.characterId,
          item.rarity,
          item.level,
          item.mutation,
          item.quantity
        )
    );

    if (statements.length) {
      await env.DB.batch(statements);
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

      const match = url.pathname.match(/^\\/trades\\/(\\d+)$/);
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
