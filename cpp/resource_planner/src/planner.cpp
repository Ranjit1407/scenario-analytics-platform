#include "planner.hpp"

#include <algorithm>
#include <cctype>
#include <functional>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <unordered_set>
#include <utility>

namespace planner {
namespace {

std::string trim(const std::string& s) {
    const auto first = s.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    const auto last = s.find_last_not_of(" \t\r\n");
    return s.substr(first, last - first + 1);
}

std::string to_lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::vector<std::string> split(const std::string& line, char sep) {
    std::vector<std::string> fields;
    std::stringstream stream(line);
    std::string field;
    while (std::getline(stream, field, sep)) fields.push_back(trim(field));
    // getline drops a trailing empty field ("a,b,"); keep it so the
    // field count check below catches the malformed line.
    if (!line.empty() && line.back() == sep) fields.emplace_back();
    return fields;
}

[[noreturn]] void fail(std::size_t line_no, const std::string& message) {
    throw std::runtime_error("line " + std::to_string(line_no) + ": " + message);
}

// std::stod accepts "12abc" and stops at the first bad character; we want the
// whole field to be a number.
double parse_number(const std::string& text, const char* field, std::size_t line_no) {
    if (text.empty()) fail(line_no, std::string(field) + " is empty");
    std::size_t consumed = 0;
    double value = 0.0;
    try {
        value = std::stod(text, &consumed);
    } catch (const std::exception&) {
        fail(line_no, std::string(field) + " is not a number: '" + text + "'");
    }
    if (consumed != text.size()) fail(line_no, std::string(field) + " is not a number: '" + text + "'");
    return value;
}

}  // namespace

std::vector<Task> parse_tasks(std::istream& in) {
    std::vector<Task> tasks;
    std::unordered_set<std::string> seen_ids;
    std::string line;
    std::size_t line_no = 0;
    bool header_checked = false;

    while (std::getline(in, line)) {
        ++line_no;
        const std::string content = trim(line);
        if (content.empty() || content.front() == '#') continue;

        // The header is optional: only skip the first data line if it looks like one.
        if (!header_checked) {
            header_checked = true;
            if (to_lower(content).rfind("task_id", 0) == 0) continue;
        }

        const auto fields = split(content, ',');
        if (fields.size() != 3) {
            fail(line_no, "expected 3 fields (task_id,volume,rate_per_hour), got " +
                              std::to_string(fields.size()));
        }

        Task task;
        task.id = fields[0];
        task.volume = parse_number(fields[1], "volume", line_no);
        task.rate_per_hour = parse_number(fields[2], "rate_per_hour", line_no);

        if (task.id.empty()) fail(line_no, "task_id is empty");
        if (task.volume < 0) fail(line_no, "volume must not be negative");
        if (task.rate_per_hour <= 0) fail(line_no, "rate_per_hour must be greater than zero");
        if (!seen_ids.insert(task.id).second) fail(line_no, "duplicate task_id '" + task.id + "'");

        tasks.push_back(std::move(task));
    }
    return tasks;
}

Plan build_plan(const std::vector<Task>& tasks, const PlanOptions& options) {
    if (options.resources == 0) throw std::invalid_argument("at least one resource is required");
    if (options.hours_per_day <= 0.0 || options.hours_per_day > 24.0) {
        throw std::invalid_argument("hours per day must be in (0, 24]");
    }

    Plan plan;
    plan.loads.resize(options.resources);
    for (std::size_t r = 0; r < options.resources; ++r) plan.loads[r].resource = r;

    // LPT order. Ties are broken by id so the output is deterministic.
    // Pointers avoid copying the task strings while sorting.
    std::vector<const Task*> order;
    order.reserve(tasks.size());
    for (const auto& task : tasks) order.push_back(&task);
    std::sort(order.begin(), order.end(), [](const Task* a, const Task* b) {
        const double ea = a->effort_hours();
        const double eb = b->effort_hours();
        return ea != eb ? ea > eb : a->id < b->id;
    });

    // Min-heap of (time the resource becomes free, resource index). When two
    // resources are free at the same time the lower index wins, again for
    // deterministic output.
    using Slot = std::pair<double, std::size_t>;
    std::priority_queue<Slot, std::vector<Slot>, std::greater<Slot>> free_at;
    for (std::size_t r = 0; r < options.resources; ++r) free_at.emplace(0.0, r);

    double longest_task = 0.0;
    plan.assignments.reserve(tasks.size());
    for (const Task* task : order) {
        const auto [start, resource] = free_at.top();
        free_at.pop();

        const double effort = task->effort_hours();
        const double end = start + effort;
        plan.assignments.push_back({task->id, resource, start, end});

        plan.loads[resource].busy_hours += effort;
        plan.loads[resource].task_count += 1;
        plan.total_effort_hours += effort;
        plan.makespan_hours = std::max(plan.makespan_hours, end);
        longest_task = std::max(longest_task, effort);

        free_at.emplace(end, resource);
    }

    // The work cannot finish before it is perfectly spread over all resources,
    // nor before the single longest task is done.
    plan.lower_bound_hours =
        std::max(plan.total_effort_hours / static_cast<double>(options.resources), longest_task);

    if (plan.makespan_hours > 0.0) {
        for (auto& load : plan.loads) load.utilization = load.busy_hours / plan.makespan_hours;
        plan.overall_utilization =
            plan.total_effort_hours / (static_cast<double>(options.resources) * plan.makespan_hours);
    }
    plan.completion_days = plan.makespan_hours / options.hours_per_day;

    std::sort(plan.assignments.begin(), plan.assignments.end(),
              [](const Assignment& a, const Assignment& b) {
                  return a.resource != b.resource ? a.resource < b.resource
                                                  : a.start_hour < b.start_hour;
              });
    return plan;
}

}  // namespace planner
