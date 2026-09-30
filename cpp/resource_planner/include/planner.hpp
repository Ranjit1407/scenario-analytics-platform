// planner.hpp - resource utilisation and completion-time estimation.
//
// Given a list of tasks (work volume + productivity rate) and a pool of
// identical resources (crews, machines, cranes, ...), build a schedule and
// report how long the work takes and how busy each resource is.
//
// Scheduling uses the Longest Processing Time (LPT) heuristic: sort tasks by
// effort, longest first, and always give the next task to the resource that
// becomes free earliest. It is simple, O(n log n), and is proven to finish
// within 4/3 of the optimal makespan, which is plenty for planning estimates.
#pragma once

#include <cstddef>
#include <istream>
#include <string>
#include <vector>

namespace planner {

struct Task {
    std::string id;
    double volume = 0.0;         // units of work, e.g. blades to inspect
    double rate_per_hour = 0.0;  // units one resource completes per hour

    double effort_hours() const { return volume / rate_per_hour; }
};

struct Assignment {
    std::string task_id;
    std::size_t resource = 0;  // 0-based index into the resource pool
    double start_hour = 0.0;   // working hours from plan start
    double end_hour = 0.0;
};

struct ResourceLoad {
    std::size_t resource = 0;
    std::size_t task_count = 0;
    double busy_hours = 0.0;
    double utilization = 0.0;  // busy_hours / makespan, in [0, 1]
};

struct PlanOptions {
    std::size_t resources = 1;
    double hours_per_day = 8.0;  // working hours per day, used to express time in days
};

struct Plan {
    std::vector<Assignment> assignments;  // ordered by resource, then start time
    std::vector<ResourceLoad> loads;      // one entry per resource
    double total_effort_hours = 0.0;
    double makespan_hours = 0.0;          // when the last task finishes
    double lower_bound_hours = 0.0;       // no schedule can finish earlier than this
    double overall_utilization = 0.0;     // total effort / (resources * makespan)
    double completion_days = 0.0;         // makespan in working days
};

// Reads "task_id,volume,rate_per_hour" lines. A header line, blank lines and
// lines starting with '#' are skipped. Throws std::runtime_error with the
// line number on malformed input.
std::vector<Task> parse_tasks(std::istream& in);

// Throws std::invalid_argument if the options are out of range.
Plan build_plan(const std::vector<Task>& tasks, const PlanOptions& options);

}  // namespace planner
