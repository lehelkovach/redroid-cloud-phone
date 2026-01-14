# ✅ Redroid VNC is Ready!

**Status:** ✓ VNC enabled and running on port 5900

---

## Connect Now

### Step 1: Create SSH Tunnel

Open a terminal and run:

```bash
ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@137.131.52.69 -N
```

**Leave this terminal open!** The `-N` flag means it won't open a shell, just creates the tunnel.

### Step 2: Connect VNC

Open another terminal and run:

```bash
vncviewer localhost:5900
```

**Password:** `redroid`

---

## What You Should See

- Android 16 home screen
- Full Android interface
- You can interact with touch/mouse

---

## Troubleshooting

### If VNC freezes:
1. Close VNC viewer
2. Stop the SSH tunnel (Ctrl+C in tunnel terminal)
3. Restart tunnel: `ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@137.131.52.69 -N`
4. Reconnect: `vncviewer localhost:5900`

### If connection fails:
- Make sure SSH tunnel is running (check the first terminal)
- Wait 10-15 seconds after starting tunnel before connecting
- Try: `vncviewer localhost::5900` (with double colon)

---

## Quick Test

Test if VNC is working:
```bash
# Check tunnel is active
netstat -an | grep 5900

# Should show: tcp 0 0 127.0.0.1:5900 LISTEN
```



