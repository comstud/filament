// Neutral load generator for the networked echo benchmark.
//
// The in-process echo benchmark runs the client and the server in one process
// on one runtime, so its number is a whole-runtime measurement: a fast client
// and a fast server are indistinguishable in it.  This drives the server from
// another machine with one fixed implementation, so the only thing that varies
// between filament, gevent and eventlet is the server.
//
// Go rather than Python because the generator must not be the bottleneck: the
// servers under test reach ~180k req/s, and a single-threaded Python selectors
// loop tops out near 218k, which is not enough headroom to trust the top end.
//
// Connection setup is deliberately OUTSIDE the measured window.  Counting it
// as throughput is what made the in-process benchmark report the platform's
// TCP behaviour as if it were echo performance.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"runtime"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

type result struct {
	Conns       int     `json:"conns"`
	PayloadSize int     `json:"payload_bytes"`
	Requests    int64   `json:"requests"`
	Seconds     float64 `json:"seconds"`
	RPS         float64 `json:"requests_per_sec"`
	P50ms       float64 `json:"p50_ms"`
	P90ms       float64 `json:"p90_ms"`
	P99ms       float64 `json:"p99_ms"`
	MaxMs       float64 `json:"max_ms"`
	Errors      int64   `json:"errors"`
	ConnectMs   float64 `json:"connect_phase_ms"`
	Target      string  `json:"target"`
	Generator   string  `json:"generator"`
	GoMaxProcs  int     `json:"gomaxprocs"`
}

func echoServer(port, size int) {
	ln, err := net.Listen("tcp", net.JoinHostPort("0.0.0.0", strconv.Itoa(port)))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "READY go-echo port=%d gomaxprocs=%d\n",
		port, runtime.GOMAXPROCS(0))
	for {
		c, err := ln.Accept()
		if err != nil {
			continue
		}
		if t, ok := c.(*net.TCPConn); ok {
			t.SetNoDelay(true)
		}
		go func(c net.Conn) {
			defer c.Close()
			buf := make([]byte, size)
			for {
				if _, err := io.ReadFull(c, buf); err != nil {
					return
				}
				if _, err := c.Write(buf); err != nil {
					return
				}
			}
		}(c)
	}
}

func pct(sorted []time.Duration, q float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	i := int(float64(len(sorted)) * q)
	if i >= len(sorted) {
		i = len(sorted) - 1
	}
	return float64(sorted[i].Nanoseconds()) / 1e6
}

func main() {
	// GOMAXPROCS is not a detail here.  This workload is one tiny blocking
	// round-trip per goroutine, so it is latency-bound, and more Ps buys only
	// scheduler churn and worse locality.  On an Apple Silicon host the default
	// also counts efficiency cores: on an 18-core M5 Max (6 performance, 12
	// efficiency) the generator does 236.7k req/s at the default 18 and 382.6k
	// at 3 -- a 1.6x understatement that silently caps every server measured
	// through it.  Sweep it, do not assume the default; the value used is
	// recorded in the result.
	procs := flag.Int("procs", 0, "GOMAXPROCS (0 = Go default). Sweep this: "+
		"the default is wrong by 1.6x on Apple Silicon")
	serve := flag.Bool("serve", false, "run a Go echo server instead of a client "+
		"(used to measure the harness ceiling: whatever this pair reaches is "+
		"the most the generator and the wire can do, so a server under test "+
		"reaching a similar number is being limited by them and not measured)")
	host := flag.String("host", "127.0.0.1", "server host")
	port := flag.Int("port", 18899, "server port")
	conns := flag.Int("conns", 100, "concurrent connections")
	size := flag.Int("size", 64, "payload bytes per request")
	warmup := flag.Duration("warmup", 2*time.Second, "unmeasured warmup")
	dur := flag.Duration("duration", 10*time.Second, "measured window")
	flag.Parse()
	if *procs > 0 {
		runtime.GOMAXPROCS(*procs)
	}

	addr := net.JoinHostPort(*host, strconv.Itoa(*port))
	if *serve {
		echoServer(*port, *size)
		return
	}
	payload := make([]byte, *size)
	for i := range payload {
		payload[i] = 'x'
	}

	// Phase 1: connect.  Not measured -- see the package comment.
	t0 := time.Now()
	sockets := make([]net.Conn, 0, *conns)
	for i := 0; i < *conns; i++ {
		c, err := net.DialTimeout("tcp", addr, 10*time.Second)
		if err != nil {
			fmt.Fprintf(os.Stderr, "dial %d/%d: %v\n", i, *conns, err)
			os.Exit(1)
		}
		if t, ok := c.(*net.TCPConn); ok {
			t.SetNoDelay(true)
		}
		sockets = append(sockets, c)
	}
	connectMs := float64(time.Since(t0).Nanoseconds()) / 1e6

	var (
		measuring atomic.Bool
		stop      atomic.Bool
		errors    atomic.Int64
		wg        sync.WaitGroup
	)
	perConn := make([][]time.Duration, *conns)
	counts := make([]int64, *conns)

	for i, c := range sockets {
		wg.Add(1)
		go func(idx int, c net.Conn) {
			defer wg.Done()
			buf := make([]byte, *size)
			lat := make([]time.Duration, 0, 1<<16)
			// Publish on EVERY exit, not just the clean one.  Returning early
			// on an error used to discard this connection's latencies, so a
			// run with any errors reported p50/p99 of 0 -- which reads as
			// "instant" rather than "no data".
			defer func() { perConn[idx] = lat }()
			for !stop.Load() {
				start := time.Now()
				if _, err := c.Write(payload); err != nil {
					// Only failures inside the measured window are real; the
					// teardown below unblocks every goroutine by deadline and
					// would otherwise report one "error" per connection.
					if measuring.Load() {
						errors.Add(1)
					}
					return
				}
				if _, err := io.ReadFull(c, buf); err != nil {
					if measuring.Load() && err != io.EOF {
						errors.Add(1)
					}
					return
				}
				if measuring.Load() {
					lat = append(lat, time.Since(start))
					counts[idx]++
				}
			}
		}(i, c)
	}

	time.Sleep(*warmup)
	measuring.Store(true)
	mt0 := time.Now()
	time.Sleep(*dur)
	measuring.Store(false)
	elapsed := time.Since(mt0)
	stop.Store(true)
	// Let in-flight requests finish, then unblock anything still parked in
	// ReadFull by deadline, before closing.  Closing first turns a clean
	// shutdown into one error per connection.
	for _, c := range sockets {
		c.SetDeadline(time.Now().Add(2 * time.Second))
	}
	wg.Wait()
	for _, c := range sockets {
		c.Close()
	}

	all := make([]time.Duration, 0, 1<<20)
	var total int64
	for i := range perConn {
		all = append(all, perConn[i]...)
		total += counts[i]
	}
	sort.Slice(all, func(a, b int) bool { return all[a] < all[b] })

	host2, _ := os.Hostname()
	res := result{
		Conns: *conns, PayloadSize: *size, Requests: total,
		Seconds: elapsed.Seconds(),
		RPS:     float64(total) / elapsed.Seconds(),
		P50ms:   pct(all, 0.50), P90ms: pct(all, 0.90),
		P99ms: pct(all, 0.99),
		MaxMs: func() float64 {
			if len(all) == 0 {
				return 0
			}
			return float64(all[len(all)-1].Nanoseconds()) / 1e6
		}(),
		Errors: errors.Load(), ConnectMs: connectMs,
		Target: addr, Generator: host2, GoMaxProcs: runtime.GOMAXPROCS(0),
	}
	b, _ := json.Marshal(res)
	fmt.Println("NETECHO_JSON:" + string(b))
}
