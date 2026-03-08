using CUDA
using HTTP
using JSON3
using Sockets

# ─── CONFIG ────────────────────────────────────────────────────────────────────
const W         = 128          # grid width  (keep small for fast JSON transfer)
const H         = 128          # grid height
const N_AGENTS  = 512
const DECAY     = 0.97f0
const DEPOSIT   = 2.5f0
const SENSE_R   = 3.0f0
const TURN_SPD  = 0.35f0
const PORT      = 8080

# Agent types: 1=Scout 2=Harvester 3=Guardian 4=Emergent 5=Rogue
const TYPE_COUNTS = [80, 200, 60, 80, 0]   # start with 0 rogues

# ─── STATE ─────────────────────────────────────────────────────────────────────
const pheromone      = CUDA.zeros(Float32, W, H)
const rogue_pher     = CUDA.zeros(Float32, W, H)
const ax             = CUDA.zeros(Float32, N_AGENTS)
const ay             = CUDA.zeros(Float32, N_AGENTS)
const adir           = CUDA.zeros(Float32, N_AGENTS)
const atype          = CUDA.zeros(Int32,   N_AGENTS)
const anomaly        = CUDA.zeros(Float32, N_AGENTS)
const quarantined    = CUDA.zeros(Int32,   N_AGENTS)

# Mutable sim state (CPU side)
mutable struct SimState
    step::Int
    rogue_count::Int
    quarantine_count::Int
    fault_x::Float32
    fault_y::Float32
    fault_active::Bool
    add_rogues::Bool
end
const STATE = SimState(0, 0, 0, 0f0, 0f0, false, false)

# ─── INIT ───────────────────────────────────────────────────────────────────────
function init_agents!()
    ax_cpu    = rand(Float32, N_AGENTS) .* W
    ay_cpu    = rand(Float32, N_AGENTS) .* H
    adir_cpu  = rand(Float32, N_AGENTS) .* 2f0 .* Float32(π)
    atype_cpu = zeros(Int32, N_AGENTS)
    idx = 1
    for (t, count) in enumerate(TYPE_COUNTS)
        for _ in 1:count
            atype_cpu[idx] = t
            idx += 1
        end
    end
    copyto!(ax, ax_cpu)
    copyto!(ay, ay_cpu)
    copyto!(adir, adir_cpu)
    copyto!(atype, atype_cpu)
    fill!(pheromone, 0f0)
    fill!(rogue_pher, 0f0)
    fill!(anomaly, 0f0)
    fill!(quarantined, 0f0)
    STATE.step = 0
    STATE.rogue_count = 0
    STATE.quarantine_count = 0
    STATE.fault_active = false
    STATE.add_rogues = false
end

# ─── GPU KERNELS ───────────────────────────────────────────────────────────────
function decay_kernel!(ph, rph, decay)
    i = (blockIdx().x - 1) * blockDim().x + threadIdx().x
    j = (blockIdx().y - 1) * blockDim().y + threadIdx().y
    if i <= size(ph,1) && j <= size(ph,2)
        ph[i,j]  = ph[i,j]  * decay
        rph[i,j] = rph[i,j] * decay
    end
    return
end

function agent_kernel!(ax, ay, adir, atype, anomaly, quarantined,
                        ph, rph, W, H, deposit, sense_r, turn_spd,
                        fault_x, fault_y, fault_active, step)
    idx = (blockIdx().x - 1) * blockDim().x + threadIdx().x
    if idx > length(ax)
        return
    end
    if quarantined[idx] == 1
        return
    end

    t   = atype[idx]
    x   = ax[idx]
    y   = ay[idx]
    dir = adir[idx]

    # Type-specific params
    speed    = t == 1 ? 2.2f0 : t == 2 ? 0.9f0 : t == 3 ? 1.4f0 : t == 4 ? 1.7f0 : 2.0f0
    dep      = t == 1 ? 1.0f0 : t == 2 ? 3.0f0 : t == 3 ? 1.5f0 : t == 4 ? 2.0f0 : 0.0f0
    turn     = t == 5 ? 0.9f0 : turn_spd
    is_rogue = t == 5

    # Fault zone — kill non-guardians
    if fault_active && t != 3
        dx = x - fault_x
        dy = y - fault_y
        if dx*dx + dy*dy < 400f0
            quarantined[idx] = 2   # fault-killed (code 2)
            return
        end
    end

    # Sense pheromone ahead (left / forward / right)
    best_val = -1f0
    best_off = 0f0
    for off in (-0.4f0, 0f0, 0.4f0)
        sd = dir + off
        sx = x + CUDA.cos(sd) * sense_r
        sy = y + CUDA.sin(sd) * sense_r
        ix = Int32(mod(floor(sx), W)) + Int32(1)
        iy = Int32(mod(floor(sy), H)) + Int32(1)
        v  = is_rogue ? rph[ix,iy] : ph[ix,iy]
        if v > best_val
            best_val = v
            best_off = off
        end
    end

    # Rogues ignore pheromone most of the time
    if is_rogue
        best_off = (CUDA.sin(Float32(step) * 0.3f0 + Float32(idx)) * 0.9f0)
    end

    dir = dir + best_off * turn + (CUDA.sin(Float32(step)*0.1f0 + Float32(idx)*0.7f0) * 0.05f0)

    # Move
    x = mod(x + CUDA.cos(dir) * speed, Float32(W))
    y = mod(y + CUDA.sin(dir) * speed, Float32(H))

    # Deposit
    ix = Int32(mod(floor(x), W)) + Int32(1)
    iy = Int32(mod(floor(y), H)) + Int32(1)
    if is_rogue
        CUDA.@atomic rph[ix,iy] += deposit * 2.0f0
        anomaly[idx] = min(anomaly[idx] + 0.08f0, 1.0f0)
    else
        CUDA.@atomic ph[ix,iy] += dep
        anomaly[idx] = max(anomaly[idx] - 0.002f0, 0.0f0)
    end

    # Veilpiercer quarantine
    if anomaly[idx] > 0.6f0 && is_rogue
        quarantined[idx] = 1
    end

    ax[idx]   = x
    ay[idx]   = y
    adir[idx] = dir
    return
end

# ─── STEP ──────────────────────────────────────────────────────────────────────
function sim_step!()
    STATE.step += 1

    # Spawn rogues if requested
    if STATE.add_rogues
        atype_cpu = Array(atype)
        ax_cpu    = Array(ax)
        ay_cpu    = Array(ay)
        # find first 12 non-rogue, non-quarantined agents and convert
        converted = 0
        for i in 1:N_AGENTS
            if atype_cpu[i] != 5 && converted < 12
                atype_cpu[i] = 5
                converted += 1
            end
        end
        copyto!(atype, atype_cpu)
        STATE.add_rogues = false
    end

    # Decay grid
    threads2d = (16, 16)
    blocks2d  = (cld(W, 16), cld(H, 16))
    @cuda threads=threads2d blocks=blocks2d decay_kernel!(
        pheromone, rogue_pher, DECAY)

    # Agent step
    threads1d = 256
    blocks1d  = cld(N_AGENTS, threads1d)
    @cuda threads=threads1d blocks=blocks1d agent_kernel!(
        ax, ay, adir, atype, anomaly, quarantined,
        pheromone, rogue_pher, W, H, DEPOSIT, SENSE_R, TURN_SPD,
        STATE.fault_x, STATE.fault_y, STATE.fault_active, STATE.step)

    CUDA.synchronize()

    # Count rogues / quarantined
    atype_cpu     = Array(atype)
    quaran_cpu    = Array(quarantined)
    STATE.rogue_count     = count(==(5), atype_cpu)
    STATE.quarantine_count = count(==(1), quaran_cpu)
end

# ─── SERIALISE ─────────────────────────────────────────────────────────────────
function build_frame()
    ph_cpu  = Array(pheromone)
    rph_cpu = Array(rogue_pher)
    ax_cpu  = Array(ax)
    ay_cpu  = Array(ay)
    at_cpu  = Array(atype)
    an_cpu  = Array(anomaly)
    qu_cpu  = Array(quarantined)

    # Downsample grid to 64×64 for fast transfer
    step = 2
    grid = Float32[ph_cpu[i,j] for i in 1:step:W, j in 1:step:H]
    rgrid = Float32[rph_cpu[i,j] for i in 1:step:W, j in 1:step:H]

    # Clamp
    max_v = max(maximum(grid), 1f0)
    grid  = min.(grid ./ max_v, 1f0)
    max_r = max(maximum(rgrid), 1f0)
    rgrid = min.(rgrid ./ max_r, 1f0)

    agents = [(
        x = ax_cpu[i] / W,
        y = ay_cpu[i] / H,
        t = at_cpu[i],
        a = an_cpu[i],
        q = qu_cpu[i]
    ) for i in 1:N_AGENTS]

    return (
        step       = STATE.step,
        w          = W ÷ step,
        h          = H ÷ step,
        grid       = vec(grid),
        rogue_grid = vec(rgrid),
        agents     = agents,
        rogue_count     = STATE.rogue_count,
        quarantine_count = STATE.quarantine_count,
        fault_active = STATE.fault_active
    )
end

# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────
const CORS_HEADERS = [
    "Access-Control-Allow-Origin"  => "*",
    "Access-Control-Allow-Methods" => "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers" => "Content-Type",
    "Content-Type"                 => "application/json"
]

function router(req::HTTP.Request)
    path = req.target

    if req.method == "OPTIONS"
        return HTTP.Response(200, CORS_HEADERS)
    end

    if path == "/frame"
        sim_step!()
        frame = build_frame()
        return HTTP.Response(200, CORS_HEADERS, body=JSON3.write(frame))

    elseif path == "/reset"
        init_agents!()
        return HTTP.Response(200, CORS_HEADERS, body="""{"ok":true}""")

    elseif path == "/rogues"
        STATE.add_rogues = true
        return HTTP.Response(200, CORS_HEADERS, body="""{"ok":true}""")

    elseif path == "/fault" && req.method == "POST"
        body = JSON3.read(String(req.body))
        STATE.fault_x      = Float32(body[:x] * W)
        STATE.fault_y      = Float32(body[:y] * H)
        STATE.fault_active = true
        return HTTP.Response(200, CORS_HEADERS, body="""{"ok":true}""")

    elseif path == "/fault/clear"
        STATE.fault_active = false
        return HTTP.Response(200, CORS_HEADERS, body="""{"ok":true}""")

    elseif path == "/status"
        status = (
            step             = STATE.step,
            rogue_count      = STATE.rogue_count,
            quarantine_count = STATE.quarantine_count,
            fault_active     = STATE.fault_active,
            gpu              = CUDA.functional() ? string(CUDA.name(CUDA.device())) : "CPU fallback"
        )
        return HTTP.Response(200, CORS_HEADERS, body=JSON3.write(status))

    else
        return HTTP.Response(404, CORS_HEADERS, body="""{"error":"not found"}""")
    end
end

# ─── MAIN ───────────────────────────────────────────────────────────────────────
println("🧫 Slime Flow GPU Server")
println("   CUDA functional: $(CUDA.functional())")
if CUDA.functional()
    println("   Device: $(CUDA.name(CUDA.device()))")
end
println("   Grid: $(W)×$(H)  |  Agents: $(N_AGENTS)")
println("   Starting on http://localhost:$(PORT)")
println("")
println("   Endpoints:")
println("     GET  /frame       — advance 1 step, return JSON frame")
println("     GET  /reset       — reset simulation")
println("     GET  /rogues      — spawn rogue agents")
println("     POST /fault       — inject fault zone {x, y} (0–1 normalized)")
println("     GET  /fault/clear — clear fault zone")
println("     GET  /status      — server info")
println("")

init_agents!()
HTTP.serve(router, Sockets.localhost, PORT)
