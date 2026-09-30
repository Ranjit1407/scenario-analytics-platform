// resource_planner - console front end for planner.hpp
//
// Usage:
//   resource_planner <tasks.csv> [--resources N] [--hours-per-day H] [--sweep MAX]
//
// --sweep MAX prints completion time and utilisation for 1..MAX resources,
// which answers the typical planning question "how many crews do we need
// to finish by day X?".

#include <cmath>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <future>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "planner.hpp"

namespace {

struct CliArgs {
    std::string tasks_file;
    planner::PlanOptions options;
    std::size_t sweep_max = 0;  // 0 = no sweep
};

void print_usage(std::ostream& out) {
    out << "Usage: resource_planner <tasks.csv> [--resources N] [--hours-per-day H] [--sweep MAX]\n"
           "\n"
           "  tasks.csv          CSV with columns task_id,volume,rate_per_hour\n"
           "  --resources N      number of identical resources available (default 1)\n"
           "  --hours-per-day H  working hours per day (default 8)\n"
           "  --sweep MAX        also compare completion times for 1..MAX resources\n";
}

// Parses a positive integer/number argument or throws with a readable message.
std::size_t parse_count(const std::string& flag, const std::string& value) {
    std::size_t consumed = 0;
    long long n = 0;
    try {
        n = std::stoll(value, &consumed);
    } catch (const std::exception&) {
        consumed = 0;
    }
    if (consumed != value.size() || n <= 0 || n > 10000) {
        throw std::invalid_argument(flag + " expects a whole number between 1 and 10000");
    }
    return static_cast<std::size_t>(n);
}

double parse_hours(const std::string& flag, const std::string& value) {
    std::size_t consumed = 0;
    double h = 0.0;
    try {
        h = std::stod(value, &consumed);
    } catch (const std::exception&) {
        consumed = 0;
    }
    if (consumed != value.size() || !(h > 0.0 && h <= 24.0)) {
        throw std::invalid_argument(flag + " expects a number greater than 0 and at most 24");
    }
    return h;
}

CliArgs parse_args(int argc, char* argv[]) {
    CliArgs args;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto next_value = [&]() -> std::string {
            if (i + 1 >= argc) throw std::invalid_argument(arg + " requires a value");
            return argv[++i];
        };

        if (arg == "--resources") {
            args.options.resources = parse_count(arg, next_value());
        } else if (arg == "--hours-per-day") {
            args.options.hours_per_day = parse_hours(arg, next_value());
        } else if (arg == "--sweep") {
            args.sweep_max = parse_count(arg, next_value());
        } else if (!arg.empty() && arg[0] == '-') {
            throw std::invalid_argument("unknown option " + arg);
        } else if (args.tasks_file.empty()) {
            args.tasks_file = arg;
        } else {
            throw std::invalid_argument("only one tasks file can be given");
        }
    }
    if (args.tasks_file.empty()) throw std::invalid_argument("missing tasks file");
    return args;
}

// Formats a point in working time as "day D +hh:mm", i.e. hh:mm worked into
// working day D (not a wall-clock time). A task that ends exactly at the end
// of a working day is reported against that day, not as +00:00 of the next.
std::string working_time(double hours, double hours_per_day) {
    long long day = static_cast<long long>(std::floor(hours / hours_per_day));
    double into_day = hours - static_cast<double>(day) * hours_per_day;
    if (day > 0 && into_day < 1e-9) {
        day -= 1;
        into_day = hours_per_day;
    }
    const long long minutes = std::llround(into_day * 60.0);
    std::ostringstream out;
    out << "day " << (day + 1) << " +" << std::setfill('0') << std::setw(2) << minutes / 60 << ':'
        << std::setw(2) << minutes % 60;
    return out.str();
}

std::string resource_name(std::size_t index) { return "R" + std::to_string(index + 1); }

void print_plan(const planner::Plan& plan, std::size_t task_count,
                const planner::PlanOptions& options) {
    std::cout << std::fixed << std::setprecision(2);
    std::cout << "\nResource plan: " << task_count << " tasks, " << options.resources
              << " resource(s), " << options.hours_per_day << " working h/day\n"
              << std::string(64, '-') << '\n'
              << "Total effort          : " << std::setw(9) << plan.total_effort_hours << " h\n"
              << "Estimated completion  : " << std::setw(9) << plan.makespan_hours << " h  ("
              << plan.completion_days << " working days)\n"
              << "Theoretical minimum   : " << std::setw(9) << plan.lower_bound_hours << " h\n"
              << "Overall utilization   : " << std::setw(9) << plan.overall_utilization * 100.0
              << " %\n";

    std::cout << "\nResource  Tasks   Busy (h)  Utilization\n";
    for (const auto& load : plan.loads) {
        std::cout << std::left << std::setw(8) << resource_name(load.resource) << std::right
                  << std::setw(7) << load.task_count << std::setw(11) << load.busy_hours
                  << std::setw(11) << load.utilization * 100.0 << " %\n";
    }

    std::cout << "\nTask                    Resource  Start (h)    End (h)  Finishes (working time)\n";
    for (const auto& a : plan.assignments) {
        std::cout << std::left << std::setw(24) << a.task_id << std::setw(8)
                  << resource_name(a.resource) << std::right << std::setw(11) << a.start_hour
                  << std::setw(11) << a.end_hour << "  "
                  << working_time(a.end_hour, options.hours_per_day) << '\n';
    }
}

void print_sweep(const std::vector<planner::Task>& tasks, const planner::PlanOptions& base,
                 std::size_t max_resources) {
    // Each resource count is an independent plan, so they are computed in
    // parallel. build_plan only reads `tasks`, which is safe to share.
    std::vector<std::future<planner::Plan>> futures;
    futures.reserve(max_resources);
    for (std::size_t n = 1; n <= max_resources; ++n) {
        planner::PlanOptions options = base;
        options.resources = n;
        futures.push_back(std::async(std::launch::async, [&tasks, options] {
            return planner::build_plan(tasks, options);
        }));
    }

    std::cout << "\nWhat-if: completion time by number of resources\n"
              << "Resources  Completion (h)  Working days  Utilization\n";
    for (std::size_t n = 1; n <= max_resources; ++n) {
        const planner::Plan plan = futures[n - 1].get();
        std::cout << std::setw(9) << n << std::setw(16) << plan.makespan_hours << std::setw(14)
                  << plan.completion_days << std::setw(11) << plan.overall_utilization * 100.0
                  << " %\n";
    }
}

}  // namespace

int main(int argc, char* argv[]) {
    CliArgs args;
    try {
        args = parse_args(argc, argv);
    } catch (const std::invalid_argument& e) {
        std::cerr << "error: " << e.what() << "\n\n";
        print_usage(std::cerr);
        return EXIT_FAILURE;
    }

    // Check explicitly: on some platforms/filesystems an ifstream "opens" a
    // directory or missing path without setting failbit, which would otherwise
    // surface as a confusing "no tasks found".
    std::error_code ec;
    if (!std::filesystem::is_regular_file(args.tasks_file, ec)) {
        std::cerr << "error: '" << args.tasks_file << "' does not exist or is not a file\n";
        return EXIT_FAILURE;
    }
    std::ifstream file(args.tasks_file);
    if (!file) {
        std::cerr << "error: cannot open '" << args.tasks_file << "'\n";
        return EXIT_FAILURE;
    }

    try {
        const auto tasks = planner::parse_tasks(file);
        if (tasks.empty()) {
            std::cerr << "error: no tasks found in '" << args.tasks_file << "'\n";
            return EXIT_FAILURE;
        }

        print_plan(planner::build_plan(tasks, args.options), tasks.size(), args.options);
        if (args.sweep_max > 0) print_sweep(tasks, args.options, args.sweep_max);
    } catch (const std::exception& e) {
        std::cerr << "error: " << args.tasks_file << ": " << e.what() << '\n';
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
