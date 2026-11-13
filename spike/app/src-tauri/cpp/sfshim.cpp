// Stockfish 19 inside the app through its Engine class: one Engine per handle, whose search
// callbacks write the lines UCIEngine would print to a queue of that handle, no std::cin or
// std::cout. Commands run on the caller's thread, as UCIEngine::loop runs them on its own; the
// search runs on the engine's threads, so several handles search at once.

#include <algorithm>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include "attacks.h"
#include "engine.h"
#include "misc.h"
#include "position.h"
#include "search.h"
#include "uci.h"

using namespace Stockfish;

namespace {

// Lines one way: the engine adds them, the reader takes them in order and meets the end once
// the queue is closed and empty.
class Lines {
   public:
    void push(std::string line) {
        std::lock_guard lock(m);
        done.push_back(std::move(line));
        cv.notify_all();
    }

    bool read(std::string& line) {
        std::unique_lock lock(m);
        cv.wait(lock, [&] { return !done.empty() || closed; });
        if (done.empty())
            return false;
        line = std::move(done.front());
        done.pop_front();
        return true;
    }

    void close() {
        std::lock_guard lock(m);
        closed = true;
        cv.notify_all();
    }

   private:
    std::mutex              m;
    std::condition_variable cv;
    std::deque<std::string> done;
    bool                    closed = false;
};

// One engine and its output; each callback prints what the UCIEngine function of the same name prints.
struct Shim {
    Lines  out;  // before engine: ~Engine stops the search threads before the queue goes
    Engine engine;
    bool   ended = false;

    Shim() {
        out.push(engine_info());  // the first line of main()
        engine.get_options().add_info_listener([this](const std::optional<std::string>& str) {
            if (str.has_value())
                info(*str);
        });
        engine.set_on_iter([this](const Engine::InfoIter& i) {
            std::stringstream ss;
            ss << "info depth " << i.depth << " currmove " << i.currmove << " currmovenumber "
               << i.currmovenumber;
            out.push(ss.str());
        });
        engine.set_on_update_no_moves([this](const Engine::InfoShort& i) {
            std::stringstream ss;
            ss << "info depth " << i.depth << " score " << UCIEngine::format_score(i.score);
            out.push(ss.str());
        });
        engine.set_on_update_full([this](const Engine::InfoFull& i) {
            std::stringstream ss;
            ss << "info depth " << i.depth << " seldepth " << i.selDepth << " multipv "
               << i.multiPV << " score " << UCIEngine::format_score(i.score);
            if (!i.bound.empty())
                ss << " " << i.bound;
            if (int(engine.get_options()["UCI_ShowWDL"]))
                ss << " wdl " << i.wdl;
            ss << " nodes " << i.nodes << " nps " << i.nps << " hashfull " << i.hashfull
               << " tbhits " << i.tbHits << " time " << i.timeMs << " pv " << i.pv;
            out.push(ss.str());
        });
        engine.set_on_start([] {});
        engine.set_on_bestmove([this](std::string_view bestmove, std::string_view ponder) {
            std::string line = "bestmove " + std::string(bestmove);
            if (!ponder.empty())
                line += " ponder " + std::string(ponder);
            out.push(line);
        });
        engine.set_on_verify_network([this](std::string_view str) { info(str); });
    }

    // UCIEngine::print_info_string.
    void info(std::string_view str) {
        for (auto& line : split(str, "\n"))
            if (!is_whitespace(line))
                out.push("info string " + std::string(line));
    }

    // The lines of a text that UCIEngine prints between sync_cout and sync_endl.
    void print(std::string_view text) {
        for (auto& line : split(text, "\n"))
            out.push(std::string(line));
    }

    // What quit does to UCIEngine::loop: the search stops, its bestmove comes, the output ends.
    void end() {
        engine.stop();
        engine.wait_for_search_finished();
        ended = true;
        out.close();
    }

    // UCIEngine::terminate_on_critical_error ends the process; here only this engine ends.
    void fail(const std::string& cmd, const std::string& message) {
        print("info string CRITICAL ERROR: Command `" + cmd + "` failed. Reason: " + message + "\n");
        end();
    }

    // The commands of UCIEngine::loop that the spike's Protocol sends, with the same code.
    // shortcut: no bench, d, eval, flip, perft or ponderhit; copy them from UCIEngine when the app needs one.
    void command(const std::string& cmd) {
        std::istringstream is(cmd);
        std::string        token;
        is >> token;

        if (ended)
            return;  // the loop has ended
        if (token == "quit")
            end();
        else if (token == "stop")
            engine.stop();
        else if (token == "uci")
        {
            std::stringstream ss;
            ss << "id name " << engine_info(true) << "\n" << engine.get_options();
            print(ss.str());
            out.push("uciok");
        }
        else if (token == "setoption")
        {
            engine.wait_for_search_finished();
            engine.get_options().setoption(is);
        }
        else if (token == "go")
        {
            info(engine.numa_config_information_as_string());
            info(engine.thread_allocation_information_as_string());
            Search::LimitsType limits;
            limits.startTime = now();
            // shortcut: depth is the only limit read, the one of the Protocol; copy UCIEngine::parse_limits for the others.
            while (is >> token)
                if (token == "depth" && !(is >> limits.depth))
                    return fail(cmd, "Invalid argument for 'depth'");
            engine.go(limits);
        }
        else if (token == "position")
        {
            std::string fen;
            is >> token;
            if (token == "startpos")
            {
                fen = StartFEN;
                is >> token;  // "moves", if any
            }
            else if (token == "fen")
                while (is >> token && token != "moves")
                    fen += token + " ";
            else
                return;
            std::vector<std::string> moves;
            while (is >> token)
                moves.push_back(token);
            if (auto err = engine.set_position(fen, moves))
                fail(cmd, err->what());
        }
        else if (token == "ucinewgame")
            // shortcut: search_clear() also resets the tablebases, which belong to the process
            // (upstream TODO: "does not work with multiple instances"); with SyzygyPath empty it
            // rewrites zeros, and the Protocol sends it before any instance searches. Revisit
            // before setting SyzygyPath or sending ucinewgame while another instance searches.
            engine.search_clear();
        else if (token == "isready")
            out.push("readyok");
        else if (!token.empty() && token[0] != '#')
            out.push("Unknown command: '" + cmd + "'. Type help for more information.");
    }
};

// OptionsMap::add numbers the options of every Engine with one static counter: one Engine is
// built at a time, and only the first lists its options on uci.
std::mutex building;

}  // namespace

extern "C" {

// A new engine at every call.
void* sf_new(void) {
    std::lock_guard lock(building);
    static bool tables = false;
    if (!tables)
    {
        // The attack tables and Zobrist keys of main() belong to the process: filled once, before
        // any engine. Tune::init is left out: sf_19 has no TUNE() parameter, so it adds no option.
        Attacks::init();
        Position::init();
        tables = true;
    }
    return new Shim;
}

void sf_send(void* h, const char* line) { static_cast<Shim*>(h)->command(line); }

// Waits for a line and returns its length, more than cap when it did not fit; -1 once the engine is gone.
int sf_read_line(void* h, char* buf, int cap) {
    std::string line;
    if (!static_cast<Shim*>(h)->out.read(line))
        return -1;
    std::memcpy(buf, line.data(), std::min(line.size(), std::size_t(std::max(cap, 0))));
    return int(line.size());
}

void sf_free(void* h) {
    Shim* s = static_cast<Shim*>(h);
    s->engine.stop();  // what the end of input does to UCIEngine::loop; ~Engine waits for the search
    delete s;
}
}
