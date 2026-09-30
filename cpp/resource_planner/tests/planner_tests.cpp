// Minimal self-contained tests; no framework needed so the exercise builds
// anywhere a C++17 compiler is available. Registered with CTest.

#include <cmath>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

#include "planner.hpp"

namespace {

int failures = 0;

void check(bool condition, const std::string& what) {
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << what << '\n';
    }
}

bool near(double a, double b) { return std::fabs(a - b) < 1e-9; }

// Tasks with rate 1/h, so volume == effort in hours.
std::vector<planner::Task> tasks_with_efforts(std::initializer_list<double> efforts) {
    std::vector<planner::Task> tasks;
    int i = 0;
    for (double e : efforts) tasks.push_back({"T" + std::to_string(++i), e, 1.0});
    return tasks;
}

void single_resource_does_everything_in_sequence() {
    const auto plan = planner::build_plan(tasks_with_efforts({3, 5, 2}), {1, 8.0});
    check(near(plan.makespan_hours, 10.0), "single resource makespan = sum of efforts");
    check(near(plan.overall_utilization, 1.0), "single resource is 100% utilised");
    check(near(plan.completion_days, 1.25), "10h at 8h/day = 1.25 days");
}

void balanced_load_splits_evenly() {
    const auto plan = planner::build_plan(tasks_with_efforts({4, 4, 2, 2}), {2, 8.0});
    check(near(plan.makespan_hours, 6.0), "4+2 / 4+2 split");
    check(near(plan.loads[0].busy_hours, 6.0) && near(plan.loads[1].busy_hours, 6.0),
          "both resources get 6h");
    check(near(plan.overall_utilization, 1.0), "perfect balance is 100% utilised");
}

void lpt_result_matches_hand_calculation() {
    // LPT: 7->R1, 6->R2, 5->R2 (6..11), 4->R1 (7..11), 3->R1 (11..14).
    // The optimum is 13 (7+5 | 6+4+3); LPT's 14 is inside its 4/3 guarantee.
    const auto plan = planner::build_plan(tasks_with_efforts({7, 6, 5, 4, 3}), {2, 8.0});
    check(near(plan.makespan_hours, 14.0), "LPT makespan");
    check(near(plan.lower_bound_hours, 12.5), "lower bound = 25h / 2");
    check(plan.makespan_hours <= 13.0 * 4.0 / 3.0, "within 4/3 of the optimum");
}

void more_resources_than_tasks_leaves_some_idle() {
    const auto plan = planner::build_plan(tasks_with_efforts({5, 1}), {4, 8.0});
    check(near(plan.makespan_hours, 5.0), "bounded by the longest task");
    check(near(plan.lower_bound_hours, 5.0), "lower bound is the longest task");
    check(plan.loads[2].task_count == 0 && plan.loads[3].task_count == 0, "two resources idle");
    check(near(plan.overall_utilization, 6.0 / 20.0), "6h of work over 4 x 5h");
}

void empty_task_list_is_handled() {
    const auto plan = planner::build_plan({}, {3, 8.0});
    check(near(plan.makespan_hours, 0.0) && near(plan.overall_utilization, 0.0),
          "no tasks gives zero makespan and no division by zero");
}

void invalid_options_throw() {
    bool threw = false;
    try {
        planner::build_plan(tasks_with_efforts({1}), {0, 8.0});
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    check(threw, "zero resources throws");
}

void parse_reads_header_comments_and_rates() {
    std::istringstream in(
        "task_id,volume,rate_per_hour\n"
        "# comment\n"
        "\n"
        "A, 10, 2\n"
        "B,3,0.5\r\n");
    const auto tasks = planner::parse_tasks(in);
    check(tasks.size() == 2, "two tasks parsed");
    check(tasks[0].id == "A" && near(tasks[0].effort_hours(), 5.0), "A = 10/2 = 5h");
    check(near(tasks[1].effort_hours(), 6.0), "B = 3/0.5 = 6h, CRLF tolerated");
}

void parse_rejects_bad_input() {
    const char* bad_inputs[] = {
        "A,10\n",         // too few fields
        "A,10,2,x\n",     // too many
        "A,ten,2\n",      // not a number
        "A,10,2x\n",      // trailing garbage
        "A,10,0\n",       // zero rate
        "A,-1,2\n",       // negative volume
        "A,1,1\nA,2,2\n", // duplicate id
        "A,1,\n",         // empty rate
    };
    for (const char* input : bad_inputs) {
        std::istringstream in(input);
        bool threw = false;
        try {
            planner::parse_tasks(in);
        } catch (const std::runtime_error&) {
            threw = true;
        }
        check(threw, std::string("rejects: ") + input);
    }
}

}  // namespace

int main() {
    single_resource_does_everything_in_sequence();
    balanced_load_splits_evenly();
    lpt_result_matches_hand_calculation();
    more_resources_than_tasks_leaves_some_idle();
    empty_task_list_is_handled();
    invalid_options_throw();
    parse_reads_header_comments_and_rates();
    parse_rejects_bad_input();

    if (failures == 0) std::cout << "All planner tests passed\n";
    return failures == 0 ? 0 : 1;
}
