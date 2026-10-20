<?php
define('CLI_SCRIPT', true);
// Usage: php seed_site.php /path/to/moodle   (a disposable test site, never a real one)
$root = $argv[1] ?? die("usage: php seed_site.php /path/to/moodle\n");
require(rtrim($root, '/') . '/config.php');
require_once($CFG->libdir . '/testing/generator/lib.php');
require_once($CFG->dirroot . '/course/lib.php');
\core\plugininfo\mod::enable_plugin('subsection', 1);
\core\session\manager::set_user(get_admin());
$gen = new testing_data_generator();

$science = $gen->create_category(['name' => 'Science']);
$humanities = $gen->create_category(['name' => 'Humanities']);

function add_course($gen, $cat, $short, $full, $sections) {
    $course = $gen->create_course(['category' => $cat->id, 'shortname' => $short, 'fullname' => $full,
        'format' => 'topics', 'numsections' => count($sections)]);
    $num = 1;
    foreach ($sections as $name => $items) {
        course_update_section($course, get_fast_modinfo($course)->get_section_info($num), ['name' => $name]);
        foreach ($items as [$type, $iname]) {
            $gen->create_module($type, ['course' => $course->id, 'section' => $num, 'name' => $iname]);
        }
        $num++;
    }
    return $course;
}
$phy = add_course($gen, $science, 'PHY1', 'Physics I', [
    'Kinematics' => [['page', 'Linear motion notes'], ['subsection', 'Exercises'], ['assign', 'Lab report']],
    'Dynamics' => [['page', "Newton's laws"], ['subsection', 'Extras']],
]);
$calc = add_course($gen, $science, 'CALC1', 'Calculus I', ['Limits' => [['book', 'Theory of limits']], 'Derivatives' => []]);
$hist = add_course($gen, $humanities, 'HIST', 'Modern History', ['Introduction' => [['forum', 'Discussion']]]);

// Activities inside the 'Exercises' subsection of PHY1.
$modinfo = get_fast_modinfo($phy);
foreach ($modinfo->get_section_info_all() as $s) {
    if ($s->component === 'mod_subsection' && $s->name === 'Exercises') {
        $gen->create_module('page', ['course' => $phy->id, 'section' => $s->section, 'name' => 'Worked examples']);
        $gen->create_module('quiz', ['course' => $phy->id, 'section' => $s->section, 'name' => 'Self-assessment quiz']);
    }
}

// A category manager (Science only) and a teacher of PHY1.
$catmgr = $gen->create_user(['username' => 'catmanager', 'password' => 'Manager123!', 'firstname' => 'Cat', 'lastname' => 'Manager']);
$managerrole = $DB->get_field('role', 'id', ['shortname' => 'manager']);
role_assign($managerrole, $catmgr->id, context_coursecat::instance($science->id));
$teacher = $gen->create_user(['username' => 'teacher', 'password' => 'Teacher123!']);
$gen->enrol_user($teacher->id, $phy->id, 'editingteacher');
set_config('passwordpolicy', 0);
echo "courses: PHY1={$phy->id} CALC1={$calc->id} HIST={$hist->id}; science={$science->id}\n";
