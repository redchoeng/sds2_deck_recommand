using BepInEx;
using BepInEx.Logging;
using HarmonyLib;
using System;
using System.Collections.Generic;
using System.Net;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using UnityEngine;

namespace STS2Overlay
{
    [BepInPlugin("com.sts2overlay.plugin", "STS2 Overlay", "1.0.0")]
    public class STS2OverlayPlugin : BaseUnityPlugin
    {
        public static ManualLogSource Log;
        private static WebSocketServer _wsServer;

        void Awake()
        {
            Log = Logger;
            Log.LogInfo("STS2 Overlay Plugin loaded");

            _wsServer = new WebSocketServer(9001);
            _wsServer.Start();

            var harmony = new Harmony("com.sts2overlay.plugin");
            harmony.PatchAll();
        }

        void OnDestroy()
        {
            _wsServer?.Stop();
        }

        // 외부에서 호출해서 오버레이로 데이터 전송
        public static void SendState(GameState state)
        {
            string json = JsonConvert.SerializeObject(state);
            _wsServer.Broadcast(json);
        }
    }

    // 게임 상태 데이터 구조
    public class GameState
    {
        public string type { get; set; }       // "deck_update" | "card_reward"
        public List<string> deck { get; set; } // 현재 덱 카드 목록
        public List<string> offered { get; set; } // 제시된 카드 3장 (card_reward 시)
    }

    // WebSocket 서버 (System.Net 기반, 단순 구현)
    public class WebSocketServer
    {
        private readonly int _port;
        private HttpListener _listener;
        private readonly List<WebSocket> _clients = new();
        private CancellationTokenSource _cts;

        public WebSocketServer(int port) => _port = port;

        public void Start()
        {
            _cts = new CancellationTokenSource();
            _listener = new HttpListener();
            _listener.Prefixes.Add($"http://localhost:{_port}/");
            _listener.Start();
            Task.Run(() => AcceptLoop(_cts.Token));
            STS2OverlayPlugin.Log.LogInfo($"WebSocket server listening on ws://localhost:{_port}");
        }

        public void Stop()
        {
            _cts?.Cancel();
            _listener?.Stop();
        }

        async Task AcceptLoop(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested)
            {
                try
                {
                    var ctx = await _listener.GetContextAsync();
                    if (ctx.Request.IsWebSocketRequest)
                    {
                        var wsCtx = await ctx.AcceptWebSocketAsync(null);
                        var ws = wsCtx.WebSocket;
                        lock (_clients) _clients.Add(ws);
                        STS2OverlayPlugin.Log.LogInfo("Overlay client connected");
                        _ = ReceiveLoop(ws, ct);
                    }
                    else
                    {
                        ctx.Response.StatusCode = 400;
                        ctx.Response.Close();
                    }
                }
                catch (Exception ex) when (!ct.IsCancellationRequested)
                {
                    STS2OverlayPlugin.Log.LogError($"Accept error: {ex.Message}");
                }
            }
        }

        async Task ReceiveLoop(WebSocket ws, CancellationToken ct)
        {
            var buf = new byte[1024];
            try
            {
                while (ws.State == WebSocketState.Open && !ct.IsCancellationRequested)
                    await ws.ReceiveAsync(new ArraySegment<byte>(buf), ct);
            }
            finally
            {
                lock (_clients) _clients.Remove(ws);
                ws.Dispose();
            }
        }

        public void Broadcast(string message)
        {
            var bytes = Encoding.UTF8.GetBytes(message);
            var segment = new ArraySegment<byte>(bytes);
            List<WebSocket> snapshot;
            lock (_clients) snapshot = new List<WebSocket>(_clients);
            foreach (var ws in snapshot)
            {
                if (ws.State == WebSocketState.Open)
                    ws.SendAsync(segment, WebSocketMessageType.Text, true, CancellationToken.None)
                      .ContinueWith(t => { }, TaskContinuationOptions.OnlyOnFaulted);
            }
        }
    }

    // ---- 게임 패치: 덱 변경 감지 ----
    // 실제 클래스명은 STS2 게임 어셈블리를 디컴파일해서 확인 후 교체 필요
    // 아래는 예시 구조 (클래스명은 게임 버전에 따라 다를 수 있음)
    [HarmonyPatch]
    public static class DeckPatches
    {
        // 카드를 덱에 추가할 때 호출되는 메서드 패치
        // TODO: 게임 어셈블리 분석 후 실제 클래스/메서드명으로 교체
        // [HarmonyPatch(typeof(DeckManager), "AddCard")]
        // [HarmonyPostfix]
        public static void AddCard_Postfix(object __instance)
        {
            try
            {
                // 덱 카드 목록 가져오기 (리플렉션으로 필드 접근)
                // var deckField = __instance.GetType().GetField("cards", ...);
                // var cards = deckField.GetValue(__instance) as List<Card>;
                // STS2OverlayPlugin.SendState(new GameState { type="deck_update", deck=cardNames });
            }
            catch (Exception ex)
            {
                STS2OverlayPlugin.Log.LogError($"DeckPatch error: {ex}");
            }
        }
    }

    // ---- 게임 패치: 카드 보상 화면 감지 ----
    [HarmonyPatch]
    public static class CardRewardPatches
    {
        // TODO: 게임 어셈블리 분석 후 실제 클래스/메서드명으로 교체
        // [HarmonyPatch(typeof(CardRewardScreen), "Show")]
        // [HarmonyPostfix]
        public static void Show_Postfix(object __instance, object cards)
        {
            try
            {
                // 제시된 카드 이름 추출
                // var offeredNames = (cards as List<Card>).Select(c => c.name).ToList();
                // STS2OverlayPlugin.SendState(new GameState { type="card_reward", offered=offeredNames });
            }
            catch (Exception ex)
            {
                STS2OverlayPlugin.Log.LogError($"CardRewardPatch error: {ex}");
            }
        }
    }
}
