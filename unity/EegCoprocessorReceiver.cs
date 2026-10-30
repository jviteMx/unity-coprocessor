using System;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

// Attach to a GameObject. Bridge UDP defaults to localhost:9877.
// Unity APIs and feature callbacks run on the main thread in Update().
public class EegCoprocessorReceiver : MonoBehaviour
{
    [Serializable] public class Value { public string name; public string channel; public bool valid; public float value; }
    [Serializable] public class Features {
        public string type; public string config_id; public double lsl_time;
        public double window_start; public bool valid; public int ica_rank;
        public int[] removed_components; public long compute_us; public Value[] values;
    }
    public int port = 9877;
    public float maxAgeSeconds = 2f;
    public Features latest;
    public event Action<Features> OnFeatures;
    private readonly ConcurrentQueue<string> queue = new ConcurrentQueue<string>();
    private UdpClient socket;
    private Thread worker;
    private volatile bool running;
    private int queued;
    private float lastReceived;
    public bool HasFreshFeatures { get { return latest != null && Time.realtimeSinceStartup-lastReceived < maxAgeSeconds; } }

    void OnEnable() {
        socket = new UdpClient(new IPEndPoint(IPAddress.Loopback, port));
        running = true;
        worker = new Thread(Receive) { IsBackground = true }; worker.Start();
    }
    void Receive() {
        while (running) {
            try {
                IPEndPoint sender = new IPEndPoint(IPAddress.Any, 0);
                byte[] data = socket.Receive(ref sender);
                if (Interlocked.Increment(ref queued) <= 64) queue.Enqueue(Encoding.UTF8.GetString(data));
                else Interlocked.Decrement(ref queued);
            } catch (SocketException) { if (!running) return; }
              catch (ObjectDisposedException) { return; }
        }
    }
    void Update() {
        string json;
        while (queue.TryDequeue(out json)) {
            Interlocked.Decrement(ref queued);
            // JsonUtility maps JSON null floats to zero. Keep invalid entries out of callbacks.
            Features message = JsonUtility.FromJson<Features>(json);
            if (message.type != "features") { latest = null; continue; }
            latest = message;
            if (!message.valid || message.values == null || Array.Exists(message.values, v => !v.valid)) { latest = null; continue; }
            lastReceived = Time.realtimeSinceStartup;
            OnFeatures?.Invoke(message);
        }
    }
    void OnDisable() {
        running = false; socket?.Close(); worker?.Join(500); socket = null;
        while (queue.TryDequeue(out _)) Interlocked.Decrement(ref queued);
        latest = null;
    }
}
