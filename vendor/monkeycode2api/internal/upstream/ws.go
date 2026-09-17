package upstream

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/sha1"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"time"

	"monkeycode2api/internal/cred"
)

// 极简 RFC6455 客户端帧类型。
const (
	wsTextMessage   = 1
	wsBinaryMessage = 2
	wsCloseMessage  = 8
	wsPingMessage   = 9
	wsPongMessage   = 10
)

const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

type wsConn struct {
	conn net.Conn
	r    *bufio.Reader
}

func (w *wsConn) ReadMessage() (int, []byte, error) {
	for {
		opcode, payload, err := w.readFrame()
		if err != nil {
			return 0, nil, err
		}
		switch opcode {
		case wsTextMessage, wsBinaryMessage:
			return int(opcode), payload, nil
		case wsPingMessage:
			_ = w.writePong(payload)
			continue
		case wsCloseMessage:
			return int(wsCloseMessage), payload, nil
		default:
			continue
		}
	}
}

func (w *wsConn) readFrame() (byte, []byte, error) {
	two := make([]byte, 2)
	if _, err := io.ReadFull(w.r, two); err != nil {
		return 0, nil, err
	}
	h1, h2 := two[0], two[1]
	opcode := h1 & 0x0f
	masked := h2&0x80 != 0
	length := uint64(h2 & 0x7f)
	switch length {
	case 126:
		var b [2]byte
		if _, err := io.ReadFull(w.r, b[:]); err != nil {
			return 0, nil, err
		}
		length = uint64(binary.BigEndian.Uint16(b[:]))
	case 127:
		var b [8]byte
		if _, err := io.ReadFull(w.r, b[:]); err != nil {
			return 0, nil, err
		}
		length = binary.BigEndian.Uint64(b[:])
	}
	var maskKey [4]byte
	if masked {
		if _, err := io.ReadFull(w.r, maskKey[:]); err != nil {
			return 0, nil, err
		}
	}
	if length > 16<<20 {
		return 0, nil, errors.New("ws frame too large")
	}
	payload := make([]byte, length)
	if _, err := io.ReadFull(w.r, payload); err != nil {
		return 0, nil, err
	}
	if masked {
		for i := range payload {
			payload[i] ^= maskKey[i&3]
		}
	}
	return opcode, payload, nil
}

func (w *wsConn) writePong(payload []byte) error {
	return w.writeFrame(wsPongMessage, payload)
}

func (w *wsConn) writeFrame(opcode byte, payload []byte) error {
	var head []byte
	if len(payload) <= 125 {
		head = []byte{0x80 | opcode, byte(len(payload))}
	} else {
		head = []byte{0x80 | opcode, 126, byte(len(payload) >> 8), byte(len(payload))}
	}
	if _, err := w.conn.Write(head); err != nil {
		return err
	}
	_, err := w.conn.Write(payload)
	return err
}

func (w *wsConn) Close() error {
	_ = w.writeFrame(wsCloseMessage, []byte{0x03, 0xe8}) // 1000 normal close
	return w.conn.Close()
}

// dialWS 建立到任务流端点的 WebSocket 连接（RFC 6455 客户端，无第三方依赖）。
// Base 为 https，故拼接的 streamUrl 使用安全 WebSocket 通道（TLS）。
func (c *Client) dialWS(ctx context.Context, a *cred.Account, wsURL string) (*wsConn, error) {
	u, err := url.Parse(wsURL)
	if err != nil {
		return nil, err
	}
	useTLS := u.Scheme == "wss"
	host, port := u.Hostname(), u.Port()
	if port == "" {
		if useTLS {
			port = "443"
		} else {
			port = "80"
		}
	}
	addr := net.JoinHostPort(host, port)

	ctxDial, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	d := net.Dialer{}
	var conn net.Conn
	if err := ctxDial.Err(); err != nil {
		return nil, err
	}
	if useTLS {
		tconn, terr := tls.DialWithDialer(&d, "tcp", addr, &tls.Config{ServerName: host})
		conn, err = tconn, terr
	} else {
		conn, err = d.DialContext(ctxDial, "tcp", addr)
	}
	if err != nil {
		return nil, err
	}
	// 握手整体受 ctx 超时约束（普通路径/ TLS 握手都适用）
	_ = conn.SetDeadline(time.Now().Add(15 * time.Second))

	key, err := randomWSKey()
	if err != nil {
		conn.Close()
		return nil, err
	}
	httpReq := &http.Request{
		Method: "GET",
		URL:    &url.URL{Scheme: "http", Host: u.Host, Path: u.Path, RawQuery: u.RawQuery},
		Host:   u.Host,
		Header: http.Header{
			"Upgrade":               {"websocket"},
			"Connection":            {"Upgrade"},
			"Sec-WebSocket-Key":     {key},
			"Sec-WebSocket-Version": {"13"},
		},
	}
	if a.CookieHeader() != "" {
		httpReq.Header.Set("Cookie", a.CookieHeader())
	}
	if ua := a.Session.UserAgent; ua != "" {
		httpReq.Header.Set("User-Agent", ua)
	}
	if err := httpReq.Write(conn); err != nil {
		conn.Close()
		return nil, err
	}

	br := bufio.NewReader(conn)
	httpResp, err := http.ReadResponse(br, httpReq)
	if err != nil {
		conn.Close()
		return nil, err
	}
	_ = httpResp.Body.Close()
	if httpResp.StatusCode != http.StatusSwitchingProtocols {
		conn.Close()
		return nil, fmt.Errorf("ws handshake failed: %d", httpResp.StatusCode)
	}
	expect := wsAccept(key)
	if got := httpResp.Header.Get("Sec-WebSocket-Accept"); got != expect {
		conn.Close()
		return nil, errors.New("ws accept header mismatch")
	}
	// 握手完成，清除 dial 阶段设置的截止时间，避免阻塞后续流式读取
	_ = conn.SetDeadline(time.Time{})
	return &wsConn{conn: conn, r: br}, nil
}

func randomWSKey() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(b[:]), nil
}

func wsAccept(key string) string {
	h := sha1.Sum([]byte(key + wsGUID))
	return base64.StdEncoding.EncodeToString(h[:])
}
